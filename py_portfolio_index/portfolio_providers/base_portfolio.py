from math import floor, ceil
from typing import Dict

from py_portfolio_index.common import print_money, print_per
from py_portfolio_index.constants import Logger
from py_portfolio_index.exceptions import PriceFetchError
from py_portfolio_index.enums import RoundingStrategy


class BaseProvider(object):
    pass

    def _get_instrument_price(self, ticker: str):
        raise NotImplementedError

    def get_instrument_price(self, ticker: str):
        try:
            return self._get_instrument_price(ticker)
        except NotImplementedError as e:
            raise e
        except Exception as e:
            raise PriceFetchError(e)

    def buy_instrument(self, ticker: str, qty: int):
        raise NotImplementedError

    def purchase_ticker_value_dict(
        self,
        to_buy: Dict[str, float],
        purchasing_power: float,
        plan_only: bool = False,
        fractional_shares: bool = True,
        skip_errored_stocks=False,
        rounding_strategy=RoundingStrategy.CLOSEST,
    ):
        purchased = 0
        target_value = sum([v for k, v in to_buy.items()])
        diff = 0
        for key, value in to_buy.items():
            try:
                price = self.get_instrument_price(key)
            except Exception as e:
                if not skip_errored_stocks:
                    raise e
                else:
                    continue
            if not price:
                price = 0
            if price == 0:
                to_buy = 0
            else:
                to_buy = value / price
            if fractional_shares:
                to_buy_units = round(to_buy, 4)
            else:
                if rounding_strategy == RoundingStrategy.CLOSEST:
                    to_buy_units = int(round(to_buy, 0))
                elif rounding_strategy == RoundingStrategy.FLOOR:
                    to_buy_units = floor(to_buy)
                elif rounding_strategy == RoundingStrategy.CEILING:
                    to_buy_units = ceil(to_buy)
                else:
                    raise ValueError(
                        "Invalid rounding strategy provided with non-fractional shares."
                    )
            purchasing = to_buy_units * price
            diff += abs(value - purchasing)
            Logger.info(f"Need to buy {to_buy_units} units of {key}.")
            if purchasing_power - purchasing < 0:
                Logger.info(
                    f"No purchasing power left, purchased {print_money(purchased)} of {print_money(target_value)}."
                )
                break

            if plan_only:
                purchasing_power = purchasing_power - purchasing
                purchased += purchasing
                Logger.info(f"{print_money(purchasing_power)} purchasing power left")
                continue
            if to_buy_units > 0.0:
                Logger.info(f"going to buy {to_buy_units} of {key}")
                try:
                    self.buy_instrument(key, to_buy_units)
                    purchasing_power = purchasing_power - purchasing
                    purchased += purchasing
                    Logger.info(f"{print_money(purchasing_power)} purchasing power left")
                except Exception as e:
                    if not skip_errored_stocks:
                        raise e
                    else:
                        continue

        Logger.info(
            f"$ diff from ideal for purchased stocks was {print_money(diff)}. {print_per(diff/target_value)} of total purchase goal."
        )
