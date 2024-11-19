from py_portfolio_index.datastores.duckdb_datastore import DuckDBDatastore
from py_portfolio_index.models import (
    RealPortfolioElement,
    Money,
)
from py_portfolio_index.portfolio_providers.local_dict import (
    LocalDictProvider,
    LocalDictNoPartialProvider,
)


def test_datastore():
    db = DuckDBDatastore("test.db")
    db.reset()

    provider1 = LocalDictProvider(
        holdings=[
            RealPortfolioElement(ticker="AAPL", units=0.5, value=Money(value=50)),
            RealPortfolioElement(ticker="UNIL", units=1.0, value=Money(value=1000)),
        ],
        cash=Money(value=800),
    )
    provider2 = LocalDictNoPartialProvider(
        holdings=[
            RealPortfolioElement(ticker="AAPL", units=1.0, value=Money(value=100)),
        ],
        cash=Money(value=200),
    )

    db.persist_holding_data(provider1.get_holdings().holdings, provider1.PROVIDER)
    db.persist_holding_data(provider2.get_holdings().holdings, provider2.PROVIDER)

    results = db.query(
        """
WHERE symbol.ticker = 'AAPL'
SELECT
    symbol.ticker,
    sum(holdings.qty) as total_holding_qty,
    sum(holdings.value) as total_holding_value
order by symbol.ticker asc;"""
    ).fetchall()
    assert results[0] == ("AAPL", 1.5, 150)
