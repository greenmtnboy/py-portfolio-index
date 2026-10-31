from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from math import ceil, floor

from py_portfolio_index.common import print_per
from py_portfolio_index.constants import Logger
from py_portfolio_index.enums import PurchaseStrategy, RoundingStrategy
from py_portfolio_index.exceptions import PriceFetchError
from py_portfolio_index.models import (
    CompositePortfolio,
    Money,
    OrderElement,
    OrderPlan,
    OrderType,
    PortfolioProtocol,
    ProviderType,
)
from py_portfolio_index.portfolio_providers.base_portfolio import BaseProvider

from .models import IdealPortfolio, IdealPortfolioElement

MIN_ORDER_SIZE = 2
MIN_ORDER_MONEY = Money(value=MIN_ORDER_SIZE)


@dataclass
class ComparisonResult:
    ticker: str
    model: Decimal
    comparison: Decimal
    actual: Money

    @property
    def diff(self):
        return self.model - Decimal(self.comparison)


def holding_value(real: PortfolioProtocol, element: IdealPortfolioElement) -> Money:
    """What the portfolio holds of an element, across all of its listings."""
    total = Money(value=0)
    for symbol in element.symbols:
        holding = real.get_holding(symbol)
        if holding:
            total += holding.value
    return total


def quote_listings(
    candidates: Mapping[str, list[str]],
    price_fetcher: Callable,
    skip_invalid: bool = True,
) -> dict[str, tuple[str, Decimal]]:
    """Price each element at the first of its candidate symbols that quotes.

    Returns the symbol and price for each element that has one. A symbol fails
    if the fetcher names it in a PriceFetchError or returns no price for it, and
    the element moves on to its next symbol.
    """
    quoted: dict[str, tuple[str, Decimal]] = {}
    pending = {k: list(v) for k, v in candidates.items() if v}
    while pending:
        current = {k: v[0] for k, v in pending.items()}
        symbols = sorted(set(current.values()))
        try:
            prices = price_fetcher(symbols)
        except PriceFetchError as e:
            if not skip_invalid:
                raise
            # with no tickers named, nothing tells the bad ones apart; the rest
            # of the round is fetched again on the next pass
            failed = set(e.tickers).intersection(symbols) or set(symbols)
            Logger.info(f"Was unable to fetch prices for {failed}, trying other listings.")
            prices = {}
        else:
            failed = {x for x in symbols if not prices.get(x)}
        for key, symbol in current.items():
            if symbol in prices and symbol not in failed:
                quoted[key] = (symbol, prices[symbol])
                del pending[key]
                continue
            remaining = [x for x in pending[key] if x not in failed]
            if remaining:
                pending[key] = remaining
            else:
                del pending[key]
    return quoted


def compare_portfolios(
    real: PortfolioProtocol,
    ideal: IdealPortfolio,
    buy_order=PurchaseStrategy.LARGEST_DIFF_FIRST,
    target_size: Decimal | int | None = None,
):
    output: dict[str, ComparisonResult] = {}
    diff = Decimal("0.0")
    selling = Decimal("0.0")
    buying = Decimal("0.0")
    target_value: Money = Money(value=Decimal(target_size)) if target_size else real.value
    for value in ideal.holdings:
        actual_value = holding_value(real, value)
        percentage = Decimal((actual_value / target_value).value)
        output[value.ticker] = ComparisonResult(
            ticker=value.ticker,
            model=value.weight,
            comparison=percentage,
            actual=actual_value,
        )
        _diff = Decimal(value.weight) - percentage
        diff += abs(_diff)
        if _diff < 0:
            selling += abs(_diff)
        else:
            buying += abs(_diff)

    Logger.info(f"Total portfolio % delta {print_per(diff)}. Overweight {print_per(selling)}, underweight {print_per(buying)}")
    if buy_order == PurchaseStrategy.LARGEST_DIFF_FIRST:
        diff_output: dict[str, ComparisonResult] = {k: v for k, v in sorted(output.items(), key=lambda item: -abs(item[1].diff))}
    elif buy_order == PurchaseStrategy.CHEAPEST_FIRST:
        diff_output = {k: v for k, v in sorted(output.items(), key=lambda item: abs(item[1].diff))}
    else:
        raise ValueError("Invalid purchase strategy")
    to_purchase = {}
    to_sell = {}
    for key, diffvalue in diff_output.items():
        if diffvalue.diff == 0:
            continue
        elif diffvalue.diff < 0:
            diff_text = "Overweight"
            to_sell[key] = target_value * diffvalue.comparison - target_value * diffvalue.model
        else:
            diff_text = "Underweight"
            to_purchase[key] = target_value * diffvalue.model - target_value * diffvalue.comparison
            # to_purchase.append(key)
        Logger.info(f"{diff_text} {key}, {print_per(diffvalue.model)} target vs {print_per(diffvalue.comparison)} actual. Should be {target_value * diffvalue.model}, is {diffvalue.actual}")
    return to_purchase, to_sell


def round_with_strategy(to_buy_currency, rounding_strategy: RoundingStrategy) -> Money:
    if rounding_strategy == RoundingStrategy.CLOSEST:
        to_buy_units = Money(value=int(round(to_buy_currency, 0)))
    elif rounding_strategy == RoundingStrategy.FLOOR:
        to_buy_units = Money(value=floor(to_buy_currency))
    elif rounding_strategy == RoundingStrategy.CEILING:
        to_buy_units = Money(value=ceil(to_buy_currency))
    else:
        raise ValueError("Invalid Rounding Strategy")
    return to_buy_units


def round_int_with_strategy(to_buy_currency, rounding_strategy: RoundingStrategy) -> int:
    if rounding_strategy == RoundingStrategy.CLOSEST:
        to_buy_units = int(round(to_buy_currency, 0))
    elif rounding_strategy == RoundingStrategy.FLOOR:
        to_buy_units = floor(to_buy_currency)
    elif rounding_strategy == RoundingStrategy.CEILING:
        to_buy_units = ceil(to_buy_currency)
    else:
        raise ValueError("Invalid Rounding Strategy")
    return to_buy_units


def generate_auto_target_size(
    real: CompositePortfolio,
    ideal: IdealPortfolio,
) -> Money:
    zero = Money(value=0)
    cash = Money(value=0)
    for input in real.portfolios:
        if input.cash is None:
            continue
        cash += max(input.cash, zero)
    in_portfolio_value = Money(value=0)
    for value in ideal.holdings:
        in_portfolio_value += holding_value(real, value)
    return in_portfolio_value + cash


def generate_sell_order(
    key: str,
    prices: dict[str, Decimal | None],
    target_value: Money,
    diffvalue: ComparisonResult,
    provider: ProviderType | None = None,
    min_order_value: Money = MIN_ORDER_MONEY,
) -> OrderElement | None:
    if round(diffvalue.diff, 4) == 0.0000:
        return None
    elif diffvalue.diff < 0:
        sell_target: Money = target_value * diffvalue.comparison - target_value * diffvalue.model
        # as with buys, raising a small excess to the minimum would oversell it
        if sell_target < min_order_value:
            return None
        _price = prices.get(key)
        if not _price:
            return None
        price = Money(value=_price)
        # whole shares, rounded down, so a sell never goes past the target
        qty = round_int_with_strategy(sell_target / price, RoundingStrategy.FLOOR)
        if qty == 0:
            return None
        return OrderElement(
            ticker=key,
            value=price * qty,
            order_type=OrderType.SELL,
            qty=qty,
            price=price,
            provider=provider,
        )
    return None


def generate_buy_order(
    min_order_value: Money,
    scaling_factor: Money,
    purchase_power: Money,
    buy_order: PurchaseStrategy,
    key: str,
    prices: dict[str, Decimal | None],
    target_value: Money,
    diffvalue: ComparisonResult,
    provider: ProviderType | None = None,
    fractional_shares: bool = True,
) -> OrderElement | None:
    if purchase_power <= 0:
        Logger.debug("No more money to spend")
        return None
    if round(diffvalue.diff, 4) == 0.0000 or not diffvalue.diff > 0:
        return None
    diff_text = "Underweight"
    gap: Money = target_value * diffvalue.model - target_value * diffvalue.comparison
    if buy_order == PurchaseStrategy.PEANUT_BUTTER:
        gap = gap * scaling_factor.decimal
    initial_buy_target: Money = Money(value=min(gap, purchase_power))
    # raising a small gap to the minimum overbuys it; leave it for a later run
    if initial_buy_target < min_order_value:
        return None
    _price = prices.get(key)
    if not _price:
        return None
    price = Money(value=_price)

    if not fractional_shares:
        qty = round_int_with_strategy(initial_buy_target / price, RoundingStrategy.FLOOR)
        if qty == 0:
            return None
        buy_target = None
    else:
        # if we can use fractional, go with the target price only
        qty = None
        buy_target = initial_buy_target

    Logger.debug(f"{diff_text} {key}, {print_per(diffvalue.model)} target vs {print_per(diffvalue.comparison)} actual. Should be {target_value * diffvalue.model}, is {diffvalue.actual}")
    return OrderElement(
        ticker=key,
        value=buy_target,
        qty=qty,
        price=price,
        order_type=OrderType.BUY,
        provider=provider,
    )


def gen_diff_and_scaling(
    buy_order: PurchaseStrategy,
    output: dict[str, ComparisonResult],
    purchase_power: Money,
    target_value: Money,
    min_order_value: Money = MIN_ORDER_MONEY,
) -> tuple[Money, dict[str, ComparisonResult]]:
    scaling_factor = Money(value=1.0)

    if buy_order == PurchaseStrategy.LARGEST_DIFF_FIRST:
        diff_output: dict[str, ComparisonResult] = {k: v for k, v in sorted(output.items(), key=lambda item: -abs(item[1].diff))}
    elif buy_order == PurchaseStrategy.CHEAPEST_FIRST:
        diff_output = {k: v for k, v in sorted(output.items(), key=lambda item: abs(item[1].diff))}
    elif buy_order == PurchaseStrategy.PEANUT_BUTTER:
        # spread the purchase power across every underweight stock in proportion to its gap;
        # gaps that would scale below the minimum order are skipped, so leave them out of
        # the denominator or their share of the cash goes unspent
        gaps = sorted((target_value * v.diff for v in output.values() if v.diff > 0), reverse=True)
        while gaps:
            total_gap = sum(gaps, Money(value=0))
            if total_gap <= purchase_power:
                break
            scaling_factor = purchase_power / total_gap
            if gaps[-1] * scaling_factor.decimal >= min_order_value:
                break
            gaps.pop()

        diff_output = {k: v for k, v in sorted(output.items(), key=lambda item: abs(item[1].diff))}
    else:
        raise ValueError("Invalid purchase strategy")
    return scaling_factor, diff_output


def generate_order_plan(
    real: PortfolioProtocol,
    ideal: IdealPortfolio,
    price_fetcher: Callable,
    buy_order=PurchaseStrategy.LARGEST_DIFF_FIRST,
    target_size: Money | float | None = None,
    purchase_power: Money | float | None = None,
    min_order_value: Money = MIN_ORDER_MONEY,
    skip_tickers: set[str] | None = None,
    fractional_shares: bool = True,
    provider: ProviderType | None = None,
    existing_orders: list[OrderElement] | None = None,
    skip_invalid: bool = True,
    include_sell_orders: bool = False,
    sell_from: PortfolioProtocol | None = None,
    known_symbols: set[str] | None = None,
) -> OrderPlan:
    """Plan orders that move real toward ideal.

    Elements with several listings are bought in the first one price_fetcher can
    quote, skipping any not in known_symbols when the provider lists what it
    trades, and sold in the listing sell_from (default real) holds the most of.
    """
    diff = Decimal("0.0")
    selling = Decimal("0.0")
    buying = Decimal("0.0")
    target_value: Money = Money(value=target_size) if target_size else real.value
    output: dict[str, ComparisonResult] = {}
    # None means no cap; zero means spend nothing
    safe_purchase_power: Money = Money(value=purchase_power if purchase_power is not None else target_value)
    current_orders = existing_orders or []
    elements: dict[str, IdealPortfolioElement] = {}
    element_of: dict[str, str] = {}
    for value in ideal.holdings:
        if skip_tickers and skip_tickers.intersection(value.symbols):
            continue
        elements[value.ticker] = value
        for symbol in value.symbols:
            element_of.setdefault(symbol, value.ticker)
    # earlier orders may be in any listing of an element
    current_order_val_map: dict[str, Money] = defaultdict(lambda: Money(value=0))
    for current_order in current_orders:
        key = element_of.get(current_order.ticker, current_order.ticker)
        if current_order.order_type == OrderType.SELL:
            current_order_val_map[key] -= current_order.inferred_value
        else:
            current_order_val_map[key] += current_order.inferred_value
    for value in elements.values():
        actual_value = holding_value(real, value)

        if value.ticker in current_order_val_map:
            actual_value += current_order_val_map[value.ticker]

        if actual_value.is_zero:
            percentage = Decimal("0.0")
        else:
            percentage = Decimal((actual_value / target_value).value)

        output[value.ticker] = ComparisonResult(
            ticker=value.ticker,
            model=value.weight,
            comparison=percentage,
            actual=actual_value,
        )
        _diff = Decimal(value.weight) - percentage
        diff += round(abs(_diff), 4)
        if _diff == 0:
            continue
        elif _diff < 0:
            selling += abs(_diff)
        else:
            buying += abs(_diff)

    Logger.info(f"Total portfolio % delta {print_per(diff)}. Overweight {print_per(selling)}, underweight {print_per(buying)}, have {purchase_power}")

    scaling_factor, diff_output = gen_diff_and_scaling(buy_order, output, safe_purchase_power, target_value, min_order_value)
    to_purchase: list[OrderElement] = []
    to_sell: list[OrderElement] = []
    # quote only what could become an order: an index runs to thousands of
    # tickers, most within a rounding error of their target
    scale = scaling_factor.decimal if buy_order == PurchaseStrategy.PEANUT_BUTTER else Decimal(1)
    single: dict[str, list[str]] = {}
    multiple: dict[str, list[str]] = {}
    seller = sell_from or real
    for k, v in diff_output.items():
        symbols = elements[k].symbols
        if v.diff > 0 and target_value * v.diff * scale >= min_order_value:
            candidates = symbols
            if known_symbols and len(symbols) > 1:
                candidates = [x for x in symbols if x in known_symbols]
        elif include_sell_orders and v.diff < 0 and target_value * -v.diff >= min_order_value:
            # sell the listing actually held, largest first
            held = {x: holding.value.decimal for x in symbols if (holding := seller.get_holding(x)) and not holding.value.is_zero}
            candidates = sorted(held, key=lambda x: -held[x])
            if not candidates:
                continue
        else:
            continue
        (single if len(symbols) == 1 else multiple)[k] = candidates
    # quote other listings apart from the rest, so a home-exchange symbol the
    # provider rejects can't fail the batch holding the domestic tickers
    quoted = quote_listings(single, price_fetcher, skip_invalid=skip_invalid)
    quoted.update(quote_listings(multiple, price_fetcher))
    price_missing = (single.keys() | multiple.keys()) - quoted.keys()
    if price_missing:
        Logger.info(f"Was unable to fetch prices for {price_missing} tickers, adding to skipped.")
        # plan again without them, so PEANUT_BUTTER doesn't hold cash back for them
        return generate_order_plan(
            real=real,
            ideal=ideal,
            price_fetcher=price_fetcher,
            buy_order=buy_order,
            target_size=target_size,
            purchase_power=purchase_power,
            min_order_value=min_order_value,
            skip_tickers=(skip_tickers.union(price_missing) if skip_tickers else set(price_missing)),
            fractional_shares=fractional_shares,
            provider=provider,
            existing_orders=current_orders,
            skip_invalid=skip_invalid,
            include_sell_orders=include_sell_orders,
            sell_from=sell_from,
            known_symbols=known_symbols,
        )
    prices: dict[str, Decimal | None] = {symbol: price for symbol, price in quoted.values()}
    order_symbol = {k: symbol for k, (symbol, _) in quoted.items()}

    if include_sell_orders:
        for key, diffvalue in diff_output.items():
            if key not in order_symbol:
                continue
            sell_order = generate_sell_order(
                key=order_symbol[key],
                prices=prices,
                target_value=target_value,
                diffvalue=diffvalue,
                provider=provider,
                min_order_value=min_order_value,
            )
            if sell_order:
                to_sell.append(sell_order)

    for key, diffvalue in diff_output.items():
        if key not in order_symbol:
            continue
        order = generate_buy_order(
            min_order_value=min_order_value,
            scaling_factor=scaling_factor,
            purchase_power=safe_purchase_power,
            buy_order=buy_order,
            key=order_symbol[key],
            prices=prices,
            target_value=target_value,
            diffvalue=diffvalue,
            provider=provider,
            fractional_shares=fractional_shares,
        )
        if order:
            if order.value:
                safe_purchase_power = safe_purchase_power - order.value
            elif order.qty and order.price:
                safe_purchase_power = safe_purchase_power - (order.price * order.qty)
            Logger.debug(f"{safe_purchase_power} left - order is {order}")
            to_purchase.append(order)
        if safe_purchase_power < min_order_value:
            break

    return OrderPlan(to_buy=to_purchase, to_sell=to_sell)


def generate_composite_order_plan(
    composite: CompositePortfolio,
    ideal: IdealPortfolio,
    purchase_order_maps: Mapping[ProviderType, PurchaseStrategy] | PurchaseStrategy,
    target_size: Money | float | None,
    min_order_value: Money = MIN_ORDER_MONEY,
    safety_threshold: Decimal = Decimal("0.95"),
    target_order_size: Money | None = None,
    include_sell_orders: bool = False,
) -> Mapping[ProviderType, OrderPlan]:
    provider_to_portfolio_map = {x.provider: x for x in composite.portfolios if x.provider}
    zero = Money(value=0)
    if target_order_size:
        purchase_power_money = {}
        for portfolio in composite.portfolios:
            if portfolio.provider:
                available = max(portfolio.cash or zero, zero)
                local_power = min(available, target_order_size)
                purchase_power_money[portfolio.provider.PROVIDER] = local_power
                target_order_size -= local_power
    else:
        purchase_power_money = {x.provider.PROVIDER: max(x.cash or zero, zero) for x in composite.portfolios if x.provider}
    Logger.debug(f"Purchase power money is {purchase_power_money}")
    providers: list[BaseProvider] = list(provider_to_portfolio_map.keys())  # type: ignore

    if isinstance(purchase_order_maps, PurchaseStrategy):
        purchase_order_maps = {x.PROVIDER: purchase_order_maps for x in providers}
    processed = set()
    # check each of our p
    output: defaultdict[ProviderType, OrderPlan] = defaultdict(lambda: OrderPlan(to_buy=[], to_sell=[]))
    skip_tickers: set[str] = set()
    for provider in providers:
        skip_tickers = skip_tickers.union(provider.get_unsettled_instruments())

    # whole-share providers go first and fractional ones fill in around them;
    # sort on the snapshot's cash, since provider.cash goes back to the broker
    purchase_order = sorted(providers, key=lambda x: (x.SUPPORTS_FRACTIONAL_SHARES, provider_to_portfolio_map[x].cash))
    orders: list[OrderElement] = []
    # listing an asset universe can cost a request, so only ask when there's a choice
    has_listings = any(x.listings for x in ideal.holdings)
    for provider in purchase_order:
        provider_purchase_power: Money = purchase_power_money.get(provider.PROVIDER) or Money(value=0)
        processed.add(provider.PROVIDER)
        port = provider_to_portfolio_map[provider]
        # build the plan across the _entire_ composite portfolio

        # if we don't know how much cash we have, skip
        Logger.info(f"Doing provider {provider.PROVIDER} with {provider_purchase_power}")
        if port.cash <= zero:
            if not include_sell_orders:
                Logger.info("No cash left to purchase")
                continue
            # selling needs no cash, so still plan this provider's sells
            local_purchase_power = zero
        else:
            local_max_spend = port.cash * safety_threshold
            local_purchase_power = min(provider_purchase_power, local_max_spend)

        purchase_plan: OrderPlan = generate_order_plan(
            ideal=ideal,
            real=composite,
            buy_order=purchase_order_maps[provider.PROVIDER],
            # rounding_strategy=RoundingStrategy.CLOSEST,
            target_size=target_size,
            purchase_power=local_purchase_power,
            min_order_value=min_order_value,
            skip_tickers=skip_tickers,
            fractional_shares=provider.SUPPORTS_FRACTIONAL_SHARES,
            price_fetcher=provider.get_instrument_prices,
            provider=provider.PROVIDER,
            existing_orders=orders,
            include_sell_orders=include_sell_orders,
            sell_from=port,
            known_symbols=known_symbols(provider) if has_listings else None,
        )
        purchase_plan.to_sell = cap_sells_to_holdings(purchase_plan.to_sell, port)
        orders += purchase_plan.all_orders
        output[provider.PROVIDER] += purchase_plan
    return output


def known_symbols(provider: BaseProvider) -> set[str] | None:
    """The symbols a provider lists as tradable, or None if it doesn't say."""
    try:
        return provider.valid_assets or None
    # broker SDKs raise their own types; without the list, every listing is quoted
    except Exception as e:  # noqa: BLE001
        Logger.info(f"Could not list {provider.PROVIDER} assets, quoting every listing: {e}")
        return None


def cap_sells_to_holdings(sells: list[OrderElement], portfolio: PortfolioProtocol) -> list[OrderElement]:
    """Sells are planned against the whole composite, but each provider can
    only sell the shares it holds itself."""
    capped: list[OrderElement] = []
    for order in sells:
        holding = portfolio.get_holding(order.ticker)
        if not holding or not order.qty:
            continue
        qty = min(order.qty, floor(holding.units))
        if qty <= 0:
            continue
        if qty != order.qty:
            order = order.model_copy(update={"qty": qty, "value": order.price * qty if order.price else None})
        capped.append(order)
    return capped


def purchase_composite_order_plan(
    orders: Mapping[ProviderType, OrderPlan],
    providers: list[BaseProvider],
    include_sell_orders: bool = False,
):
    for provider in providers:
        if provider.PROVIDER in orders:
            provider.purchase_order_plan(orders[provider.PROVIDER], include_sell_orders=include_sell_orders)
        else:
            Logger.info(f"Provider {provider.PROVIDER} has no orders to execute")
    return True
