## Py Portfolio Index

`py-portfolio-index` is a python library to make it easier to mantain a broad, index-based approach to stock investing
while following individual ESG (Environmental, Social, and Governance) or SRI (Socially Response Investing) goals.

To do that, it provides tools for constructing and managing portfolios that are modeled off indexes. These ideal
portfolios can be efficiently converted into actual portfolios by API, using commission free platforms like Robinhood or
Alpaca. Since constructing an index analogue typically requires many small stock purchases, a
commission free platform is important to minimizing overhead. Robinhood has the additional benefit of allowing fractional
share purchases, which allow an index to come closer to an ideal match with a smaller total portfolio size. 

#### Install

The package supports Python 3.7 plus.

`pip install py-portfolio-index` [Not yet!]

#### Considerations

Some providers may take some time to place an order. Keep this in mind when running repeated rebalances, as the
portfolio balance may not have updated to reflect your last order.

#### Basic Example

This example shows a basic example using the Alpaca API in paper trading mode.

It constructs an ideal portfolio based on the composition of the Vanguard ESG index fund in Q4 2020, then uses the
Alpaca API to construct a matching portfolio based on an initial investment of 10000 dollars.

Since Alpaca doesn't support fractional shares, this portfolio will approximate an index, but cannot match it exactly, 
especially for small total portfolio sizes.

```python
from py_portfolio_index import INDEXES, STOCK_LISTS, Logger, AlpacaProvider, PurchaseStrategy, compare_portfolios,

AlpacaProvider

from logging import INFO, StreamHandler

Logger.addHandler(StreamHandler())
Logger.setLevel(INFO)

API_KEY = '#########'

SECRET_KEY = '##########'

# The size of our paper portfolio
TARGET_PORTFOLIO_SIZE = 10000

# instantiate the Alpaca provider with identity information
# and set it to use the paper provider
provider = AlpacaProvider(key_id=API_KEY, secret_key=SECRET_KEY, paper=True)

# get an example index 
# this one is the vanguard ESG index for Q4 202
ideal_portfolio = INDEXES['esgv_2020_q4']

# exclude all stocks from the oil, vice, and cruise lists
ideal_portfolio.exclude(STOCK_LISTS['oil']).exclude(STOCK_LISTS['vice']).exclude(STOCK_LISTS['cruises'])

# double the weighting of stocks in the renewable and semiconductor lists, and set them to a minimum weight of .1%
ideal_portfolio.reweight(STOCK_LISTS['renewable'], weight=2.0, min_weight=.001)
ideal_portfolio.reweight(STOCK_LISTS['semiconductor'], weight=2.0, min_weight=.001)

# get actual holdings
real_port = provider.get_holdings()

# compare actual holdings to this ideal portfolio to produce a buy and sell list
to_buy, to_sell = compare_portfolios(ideal=ideal_portfolio, real=real_port,
                                     buy_order=PurchaseStrategy.LARGEST_DIFF_FIRST,
                                     target_size=TARGET_PORTFOLIO_SIZE)

# purchase the buy list
provider.purchase_ticker_value_dict(to_buy, TARGET_PORTFOLIO_SIZE, fractional_shares=False, skip_errored_stocks=True)
```

### Robinhood

Since robinhood supports fractional shares, it's much easier to get close to an index
with a smaller amount of money. 


```python
from py_portfolio_index.bin import INDEXES, STOCK_LISTS
from py_portfolio_index.enums import PurchaseStrategy
from py_portfolio_index.operators import compare_portfolios
from py_portfolio_index.portfolio_providers.robinhood import RobinhoodProvider
from py_portfolio_index.constants import Logger

from logging import INFO, StreamHandler
Logger.addHandler(StreamHandler())
Logger.setLevel(INFO)

ideal_port = INDEXES['esgv_2020_q4']

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

to_buy, to_sell = compare_portfolios(ideal=ideal_port, real=real_port, buy_order=PurchaseStrategy.CHEAPEST_FIRST,
                                     target_size = TARGET_SIZE)


provider.purchase_ticker_value_dict(to_buy, purchasing_power=1000, fractional_shares=True, skip_errored_stocks=True)

```

### Testing

To avoid actually purchasing a stock, use the plan_only option to log what trades would have occurred.

```python

provider.purchase_ticker_value_dict(to_buy, TARGET_PORTFOLIO_SIZE, plan_only=True, fractional_shares=False,
                                    skip_errored_stocks=True, )

```

### Example Scripts

Can be found in the examples folder.

### Logging

To see messages, it's helpful to configure the logger to print messages. You can either configure the standard python
logger or use the portfolio specific one using an example like the below.

```python
from py_portfolio_index.constants import Logger
from logging import INFO, StreamHandler

Logger.addHandler(StreamHandler())
Logger.setLevel(INFO)

```



