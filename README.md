[![Discord](https://img.shields.io/badge/DISCORD-CHAT-red?logo=discord)](https://discord.gg/bNsx3cF7V4)

## Customized Index Funds

`py-portfolio-index` is a python library to make it easier to mantain a broad, index-based approach to stock investing while being able to layer in personal preferences, such as to exclude or reweight certain kinds of stocks.

 For example, a user could construct a portfolio that matches the composition of the S&P 500, but excludes oil companies and overweights semiconductor companies.

To do that, it provides tools for constructing and managing portfolios that are modeled off indexes. These ideal
portfolios can be efficiently converted into actual portfolios by API, using commission free platforms like Robinhood, Alpaca, or Webull. Since constructing an index analogue typically requires many small stock purchases, a
commission free platform is important to minimizing overhead. For small investment sizes, the ability of the platform to support fractional shares is critical to being able to accurately map to the index.

## Indexes

py-portfolio-index contains a default set of indexes, which can be access via the INDEXES dictionary: `from py_portfolio_index import INDEXES`. These indexes are based on the common industry index cuts such as 
Large Cap or real-estate and are updated quarterly.

## Lists/Themes

py-portfolio-index also contains a default list of stock lists, which can be access via the STOCK_LISTS dictionary: `from py_portfolio_index import STOCK_LISTS`. These lists are thematic groupings, such as by industry (oil, space)
or by other criteria (vice). Lists can be applied to modify indexes or reweight them to create customized
portfolios. 

#### Install

The package supports Python 3.9+.

`pip install py-portfolio-index`

Note that provider dependencies must be installed independently for each provider you wish to use.

- alpaca - `pip install alpaca-trade-api` or `pip install py-portfolio-index[alpaca]`
- robinhood - `pip install robin_stocks` or `pip install py-portfolio-index[robinhood]`
- webull - `pip install py-portfolio-index[webull]` (see the [Webull](#webull) section for a Python 3.12+ caveat)
- scwhab - `pip install schwab-py` or `pip install py-portfolio-index[schwab]`
- etrade - `pip install requests-oauthlib` or `pip install py-portfolio-index[etrade]`

#### Considerations

Default index construction uses market orders and assumes an accumulative portfolio. 

Some market information may be internally cached for up to an hour to improve performnace. py-portfolio-index is not designed for active day-trading. 

Some providers may take some time to place an order. Keep this in mind when running repeated rebalances, as the
portfolio balance may not have updated to reflect your last order.

Remember that the stock markets are not always open! Providers may vary in their treatment of market hours. 



#### Basic Example

This example shows a basic example using the Alpaca API in paper trading mode.

It constructs an ideal portfolio based on the composition of the Vanguard ESG index fund in Q4 2020, then uses the
Alpaca API to construct a matching portfolio based on an initial investment of 10000 dollars.


```python
from py_portfolio_index import INDEXES, STOCK_LISTS, Logger, AlpacaProvider, PurchaseStrategy, generate_order_plan

from logging import INFO, StreamHandler

Logger.addHandler(StreamHandler())
Logger.setLevel(INFO)


# The size of our paper portfolio
TARGET_PORTFOLIO_SIZE = 10000

# instantiate the Alpaca provider with identity information
# and set it to use the paper provider
# this expects the environment variables ALPACA_API_KEY and ALPACA_API_SECRET to be set,
# or they can be passed in directly, using AlpacaProvider(key_id=..., secret_key=...)
provider = AlpacaProvider()

# get an example index 
ideal_portfolio = INDEXES['small_cap']

# exclude all stocks from the oil, vice, and cruise lists
ideal_portfolio.exclude(STOCK_LISTS['oil']).exclude(STOCK_LISTS['vice']).exclude(STOCK_LISTS['cruises'])

# double the weighting of stocks in the renewable and semiconductor lists, and set them to a minimum weight of .1%
ideal_portfolio.reweight(STOCK_LISTS['renewable'], weight=2.0, min_weight=.001)
ideal_portfolio.reweight(STOCK_LISTS['semiconductor'], weight=2.0, min_weight=.001)

# get actual holdings
real_port = provider.get_holdings()

# compare actual holdings to this ideal portfolio to produce a buy and sell list
planned_orders = generate_order_plan(ideal=ideal_portfolio, real=real_port,
                                     buy_order=PurchaseStrategy.LARGEST_DIFF_FIRST,
                                     target_size=TARGET_PORTFOLIO_SIZE)
# review the orders
for item in planned_orders.to_buy:
    print(item)

# purchase the buy list
provider.purchase_order_plan(plan = planned_orders, fractional_shares=False, skip_errored_stocks=False)
```

[!TIP]
You can set environment variables to avoid having to pass in your credentials each time. THese are specified per provider. For Alpaca, you can set ALPACA_API_KEY and ALPACA_API_SECRET.


### Robinhood

Robinhood is also commission free and supports fractional shares.


```python
from py_portfolio_index import RobinhoodProvider, PurchaseStrategy, compare_portfolios, Logger,  INDEXES, STOCK_LISTS

from logging import INFO, StreamHandler
Logger.addHandler(StreamHandler())
Logger.setLevel(INFO)

ideal_port = INDEXES['small_cap']

# create a stock list
STOCK_LISTS.add_list('manual_override', ['MDLZ'])

# modify the index
ideal_port.exclude(STOCK_LISTS['oil']).exclude(STOCK_LISTS['vice']).exclude(STOCK_LISTS['cruises']).exclude(
    STOCK_LISTS['manual_override'])

# overweight on stonks
ideal_port.reweight(STOCK_LISTS['renewable'], weight=2.0, min_weight=.001)
ideal_port.reweight(STOCK_LISTS['semiconductor'], weight=2.0, min_weight=.001)

provider = RobinhoodProvider(username='#####', password='#########')

real_port = provider.get_holdings()

TARGET_SIZE = 10000

planned_orders = generate_order_plan(ideal=ideal_portfolio, real=real_port,
                                     buy_order=PurchaseStrategy.LARGEST_DIFF_FIRST,
                                     target_size=TARGET_PORTFOLIO_SIZE)
# review the orders
for item in planned_orders.to_buy:
    print(item)

# purchase the buy list
provider.purchase_order_plan(plan = planned_orders, fractional_shares=False, skip_errored_stocks=False)

```

### Webull

Webull support is mature. Follow similar patterns to the above examples, but use the WebullProvider.

This uses the [official Webull OpenAPI SDK](https://github.com/webull-inc/openapi-python-sdk). Generate an
app key and app secret from the developer portal for your region, then pass them in or set
`WEBULL_API_KEY` and `WEBULL_API_SECRET`.

```python

from py_portfolio_index import WebullProvider

provider = WebullProvider()

# or explicitly
provider = WebullProvider(app_key="...", app_secret="...", region_id="us")
```

If your credentials cover more than one Webull account, pass `account_id=` or set `WEBULL_ACCOUNT_ID`;
otherwise the first account is used and a warning is logged naming it.

#### Installation on Python 3.12+

`webull-python-sdk-mdata` and `webull-python-sdk-trade` pull in `grpcio==1.51.1` (via
`webull-python-sdk-quotes-core` and `webull-python-sdk-trade-events-core`). That version has no wheel for
recent Pythons and fails to build from source. Only the gRPC and MQTT streaming modules need it, and this
library uses neither, so install those two packages without their dependencies:

```bash
pip install webull-python-sdk-core requests
pip install --no-deps webull-python-sdk-mdata webull-python-sdk-trade
```

The SDK also vendors a copy of `requests` and `six` that cannot be imported on Python 3.12+ (the vendored
`requests` imports the removed `cgi` module, and the vendored `six` registers an import hook that no longer
exists). `py_portfolio_index.portfolio_providers.helpers.webull` transparently redirects those vendored
module names at the real libraries, so no action is needed beyond having `requests` installed.

#### Unsupported operations

Webull's OpenAPI exposes no dividend endpoint, and only surfaces the current day's orders for US accounts.
`get_dividend_details`, `_get_dividends` and `get_transactions` therefore raise `NotImplementedError`, and
per-ticker profit reports appreciation only. There is also no paper-trading endpoint, so
`WebullPaperProvider` raises `ConfigurationError`.


### Schwab

Schwab support is experimental. Follow similar patterns to the above examples, but use the ScwhabProvider.

This currently uses [this unoffical API package](https://github.com/alexgolec/schwab-py), and requires you
to create an app on the schwab website and follow the authorization path from their docs.

```python

from py_portfolio_index import SchwabProvider

```

### E*TRADE

E*TRADE support is experimental. Follow similar patterns to the above examples, but use the ETradeProvider.

This talks to the [E*TRADE v1 REST API](https://developer.etrade.com/home) directly over OAuth 1.0a.
Request an API key and secret from the E*TRADE developer portal, then either pass them in or set
`ETRADE_API_KEY` and `ETRADE_API_SECRET`.

```python

from py_portfolio_index import ETradeProvider

# opens a browser to authorize; paste the verification code back when prompted
provider = ETradeProvider()

# sandbox keys should target the sandbox environment
provider = ETradeProvider(sandbox=True)  # or set ETRADE_SANDBOX=true

```

Authorization notes:

- E*TRADE's default OAuth setup only supports the out-of-band flow: a browser opens to E*TRADE,
  and you paste the displayed verification code back into the terminal. E*TRADE will register a
  redirect callback URL for your app on request to their API support team; once registered, pass a
  custom `verifier_func` to capture the `oauth_verifier` from the redirect instead of prompting.
- Access tokens expire at midnight US Eastern every day, so expect one authorization prompt per
  day. Within a day, tokens are cached and renewed automatically.

If your credentials cover more than one account, pass `account_id=` or set `ETRADE_ACCOUNT_ID`
(either the numeric account id or the accountIdKey work); otherwise the first active account is
used and a warning is logged.

E*TRADE's API only accepts whole-share equity orders (no fractional shares), and has no historical
price endpoint, so date-based lookups raise `NotImplementedError`. Dividend history and transactions
are built from the account transactions endpoint.

## Composite Portfolios

To purchase a 'composite' portfolio - where you build an index across multiple providers - use the composite helpers. 


An example of purchasing across both Webull and Alpaca. 

```python

from py_portfolio_index import  CompositePortfolio, generate_composite_order_plan, purchase_composite_order_plan, WebullProvider, AlpacaProvider, INDEXES

ideal_portfolio = INDEXES['small_cap']

TARGET_SIZE = 100_000

providers = [AlpacaProvider(), WebullProvider()]

holdings = [p.get_holdings() for p in providers]

composite = CompositePortfolio(holdings)

planned_orders = generate_composite_order_plan(ideal=ideal_port, composite = composite,
                                    purchase_order_maps=PurchaseStrategy.LARGEST_DIFF_FIRST,
                                    target_size=TARGET_SIZE)

print(planned_orders)
# uncomment to purchase
# purchase_composite_order_plan(planned_orders, providers)

```

### Testing

To avoid actually purchasing a stock, use the plan_only option to log what trades would have occurred.

```python

provider.purchase_order_plan(plan = planned_orders, plan_only=True )

```

### Example Scripts

Can be found in the examples folder.

### Logging

It can be helpful to configure the logger to print messages. You can either configure the standard python logger or use the portfolio specific one using an example like the below.

Relevant messages are at both INFO and DEBUG levels. 

```python
from py_portfolio_index.constants import Logger
from logging import INFO, StreamHandler

Logger.addHandler(StreamHandler())
Logger.setLevel(INFO)

```

