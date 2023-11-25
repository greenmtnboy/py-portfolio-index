from py_portfolio_index import PaperAlpacaProvider
from datetime import date


def test_provider_methods():
    providers = [PaperAlpacaProvider()]
    # if "robinhood" in AVAILABLE_PROVIDERS:
    #     providers.append(RobinhoodProvider())
    test_date = date(year=2023, month=1, day=1)
    for provider in providers:
        provider.get_holdings()
        real_ticker = "MSFT"
        p1 = provider.get_instrument_price(real_ticker)
        p2 = provider.get_instrument_prices([real_ticker])
        assert p1 == p2[real_ticker]

        p1 = provider.get_instrument_price(real_ticker, at_day=test_date)
        p2 = provider.get_instrument_prices([real_ticker], at_day=test_date)
        assert p1 == p2[real_ticker]

        provider.get_unsettled_instruments()

        provider.get_stock_info(real_ticker)


# def test_stock_reweighting():
#     provider = PaperAlpacaProvider()

#     portfolio = IdealPortfolio(source_date="2021-01-01")
#     por
