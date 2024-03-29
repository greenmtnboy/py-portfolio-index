from py_portfolio_index import (
    INDEXES,
    STOCK_LISTS,
    RobinhoodProvider,
    PurchaseStrategy,
    compare_portfolios,
)

ideal_port = INDEXES["esgv"]

ideal_port.exclude(STOCK_LISTS["oil_sector"]).exclude(STOCK_LISTS["vice"]).exclude(
    STOCK_LISTS["cruises"]
)
ideal_port.reweight(STOCK_LISTS["renewable"], weight=2.0, min_weight=0.001)
ideal_port.reweight(STOCK_LISTS["semiconductor"], weight=2.0, min_weight=0.001)

USERNAME = "######"
PASSWORD = "######"

provider = RobinhoodProvider(USERNAME, PASSWORD)

real_port = provider.get_holdings()

to_buy, to_sell = compare_portfolios(
    ideal=ideal_port,
    real=real_port,
    buy_order=PurchaseStrategy.LARGEST_DIFF_FIRST,
    target_size=160000,
)

provider.purchase_ticker_value_dict(to_buy, 700, fractional_shares=True)
