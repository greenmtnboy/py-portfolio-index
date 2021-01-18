from math import floor
from typing import Dict

from py_portfolio_index.common import print_flat_money
from py_portfolio_index.constants import Logger
from py_portfolio_index.exceptions import PriceFetchError


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
    ):
        purchased = 0
        target_quantity = sum([v for k, v in to_buy.items()])
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
                to_buy_units = floor(to_buy)
            purchasing = to_buy_units * price

            purchasing_power = purchasing_power - purchasing

            Logger.info(f"Need to buy {to_buy_units} units of {key}.")
            if purchasing_power < 0:
                Logger.info(
                    f"No purchasing power left, purchased {print_flat_money(purchased)} of {print_flat_money(target_quantity)}."
                )
                break
            purchased += purchasing
            Logger.info(f"{print_flat_money(purchasing_power)} purchasing power left")
            if plan_only:
                continue
            if to_buy_units > 0:
                Logger.info(f"going to buy {to_buy_units} of {key}")
                self.buy_instrument(key, to_buy_units)
