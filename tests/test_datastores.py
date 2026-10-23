import os

import pytest

from py_portfolio_index.datastores.duckdb_datastore import DuckDBDatastore
from py_portfolio_index.enums import ProviderType
from py_portfolio_index.models import (
    Money,
    RealPortfolioElement,
)
from py_portfolio_index.portfolio_providers.local_dict import (
    LocalDictNoPartialProvider,
    LocalDictProvider,
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


def holding_rows(db: DuckDBDatastore):
    return db.executor.execute_raw_sql(
        "SELECT symbol, provider, qty, value FROM ticker_holdings ORDER BY provider, symbol;"
    ).fetchall()


def test_datastore_repeat_persist_replaces_snapshot(tmp_path):
    db = DuckDBDatastore(str(tmp_path / "repeat.db"))
    db.reset()
    try:
        first = [
            RealPortfolioElement(ticker="AAPL", units=1.0, value=Money(value=100)),
            RealPortfolioElement(ticker="NOTAREALTICKERX", units=2.0, value=Money(value=50)),
        ]
        # unrecognized tickers pool into one row; persisting again used to
        # collide on that row's primary key
        db.persist_holding_data(first, ProviderType.SCHWAB)
        db.persist_holding_data(first, ProviderType.SCHWAB)
        assert len(holding_rows(db)) == 2

        # AAPL was sold: it should not linger from the earlier snapshot
        second = [
            RealPortfolioElement(ticker="NOTAREALTICKERX", units=3.0, value=Money(value=75)),
        ]
        db.persist_holding_data(second, ProviderType.SCHWAB)
        assert holding_rows(db) == [(-1, 5, 3.0, 75.0)]
    finally:
        db.close()


def test_datastore_other_providers_untouched(tmp_path):
    db = DuckDBDatastore(str(tmp_path / "isolation.db"))
    db.reset()
    try:
        aapl = [RealPortfolioElement(ticker="AAPL", units=1.0, value=Money(value=100))]
        db.persist_holding_data(aapl, ProviderType.ALPACA)
        db.persist_holding_data(aapl, ProviderType.SCHWAB)
        db.persist_holding_data([], ProviderType.SCHWAB)
        assert [(r[1]) for r in holding_rows(db)] == [2]
    finally:
        db.close()


def test_datastore_etrade(tmp_path):
    db = DuckDBDatastore(str(tmp_path / "etrade.db"))
    db.reset()
    try:
        # simulate a database created before E*TRADE was supported
        db.executor.execute_raw_sql("DELETE FROM providers WHERE id = 6;")
        aapl = [RealPortfolioElement(ticker="AAPL", units=1.0, value=Money(value=100))]
        db.persist_holding_data(aapl, ProviderType.ETRADE)
        assert db.executor.execute_raw_sql(
            "SELECT name FROM providers WHERE id = 6;"
        ).fetchall() == [("E*TRADE",)]
        assert holding_rows(db)[0][1] == 6
    finally:
        db.close()
