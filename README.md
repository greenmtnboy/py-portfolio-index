
### Py Portfolio Index

`py-portfolio-index` is a python library to make it easier to mantain a broad, index-based approach to stock investing
while following individual ESG (Environmental, Social, and Governance) or SRI (Socially Response Investing) goals. 

To do that, it provides tools for constructing and managing portfolios that are modeled off indexes. These ideal 
portfolios can be efficiently converted into actual portfolios by API, using commission free platforms like 
Robinhood or Alpaca.

#### Install
The package supports Python 3.7 plus.

`pip install py-portfolio-index`

#### Considerations
Some providers may take some time to place an order. Keep this in mind when running repeated rebalances, as
the portfolio balance may not have updated to reflect your last order.


#### Basic Example

This example shows a basic example using the Alpaca API in paper trading mode. 

It constructs an ideal portfolio based on the composition of the Vanguard ESG index fund in Q4 2020,
then uses the Alpaca API to construct a matching portfolio based on an initial investment of 10000 dollars.

Since Alpaca doesn't support fractional shares, this portfolio will be close to the index, but not exact.

```python
from py_portfolio_index.bin import INDEXES, STOCK_LISTS
from py_portfolio_index.constants import Logger
from py_portfolio_index.enums import BuyOrder
from py_portfolio_index.operators import compare_portfolios
from py_portfolio_index.portfolio_providers.alpaca import AlpacaProvider

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
to_buy, to_sell = compare_portfolios(ideal=ideal_portfolio, real=real_port, buy_order=BuyOrder.LARGEST_DIFF_FIRST,
                                     target_size=TARGET_PORTFOLIO_SIZE)

# purchase the buy list
provider.purchase_ticker_value_dict(to_buy, TARGET_PORTFOLIO_SIZE, fractional_shares=False, skip_errored_stocks=True)
```

#### Example Scripts

Can be found in the examples folder.

#### Logging

To see messages, it's helpful to configure the logger to print messages. You can either configure the
standard python logger or use the portfolio specific one using an example like the below.

```python
from py_portfolio_index.constants import Logger
from logging import INFO, StreamHandler

Logger.addHandler(StreamHandler())
Logger.setLevel(INFO)

```



