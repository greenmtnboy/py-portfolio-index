from py_portfolio_index import PaperAlpacaProvider


def test_provider_methods():
    providers = [PaperAlpacaProvider()]
    # if "robinhood" in AVAILABLE_PROVIDERS:
    #     providers.append(RobinhoodProvider())

    for provider in providers:
        provider.get_holdings()
        real_ticker = "MSFT"
        p1 = provider.get_instrument_price(real_ticker)
        p2 = provider.get_instrument_prices([real_ticker])
        assert p1 == p2[real_ticker]

        provider.get_unsettled_instruments()

        provider.get_stock_info(real_ticker)
