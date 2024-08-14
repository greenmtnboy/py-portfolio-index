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
- webull - `pip install webull` or `pip install py-portfolio-index[webull]`
- scwhab - `pip install schwab-py` or `pip install py-portfolio-index[schwab]`

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

This currently uses [this unoffical API package](https://github.com/tedchou12/webull), and requires you 
to follow device-id authorization path from their docs. 

```python

from py_portfolio_index import WebullProvider

```


### Schwab

Schwab support is experimental. Follow similar patterns to the above examples, but use the ScwhabProvider.

This currently uses [this unoffical API package](https://github.com/alexgolec/schwab-py), and requires you
to create an app on the schwab website and follow the authorization path from their docs.

```python

from py_portfolio_index import SchwabProvider

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



