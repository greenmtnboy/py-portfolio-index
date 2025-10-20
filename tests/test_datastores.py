from py_portfolio_index.datastores.duckdb_datastore import DuckDBDatastore
from py_portfolio_index.models import (
    RealPortfolioElement,
    Money,
)
from py_portfolio_index.portfolio_providers.local_dict import (
    LocalDictProvider,
    LocalDictNoPartialProvider,
)
import os
import pytest


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


def test_datastore_close():
    db_path = "test_two.db"
    db = DuckDBDatastore(db_path)
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

    db.close()

    # validate all connections to test.db are gone and the file is not locked
    # Try to delete the file - this will fail if any handles are open
    try:
        if os.path.exists(db_path):
            os.remove(db_path)
        # If we get here, the file was successfully deleted (unlocked)
        assert True
    except PermissionError:
        pytest.fail(f"Database file {db_path} is still locked after close()")
    finally:
        # Cleanup: remove file if it still exists
        if os.path.exists(db_path):
            os.remove(db_path)
