# from py_portfolio_index import SchwabProvider
# from py_portfolio_index.portfolio_providers.helpers.schwab import fetch_response, create_login_context
# from datetime import date


# def test_schwab_async_login():

#     login_context = create_login_context()
#     # you actually need
#     print(login_context.auth_url)
#     fetch_response(login_context)
#     test = SchwabProvider(external_auth=True)

#     assert test.get_instrument_price("AAPL", date(2021, 1, 1)) is not None
