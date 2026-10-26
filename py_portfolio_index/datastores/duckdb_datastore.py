import hashlib
from collections import defaultdict
from decimal import Decimal
from typing import ClassVar

from py_portfolio_index.constants import UNKNOWN_TICKER
from py_portfolio_index.datastores.base_datastore import BaseDatastore
from py_portfolio_index.enums import ProviderType
from py_portfolio_index.models import DividendResult, RealPortfolioElement


def get_integer_id(value):
    """
    Generate a consistent integer ID from a hash of the given value.

    :param value: The input value (e.g., string, number) to hash
    :return: An integer ID
    """
    # Ensure the value is a string before hashing
    value_str = str(value).encode("utf-8")
    hash_object = hashlib.sha256(value_str)  # Use SHA-256 hash function
    hash_int = int(hash_object.hexdigest(), 16)  # Convert hex digest to an integer
    return hash_int % (2**31)


def map_provider(ptype: ProviderType):
    return {
        ProviderType.LOCAL_DICT_NO_PARTIAL: -2,
        ProviderType.LOCAL_DICT: -1,
        ProviderType.ROBINHOOD: 1,
        ProviderType.ALPACA: 2,
        ProviderType.WEBULL: 3,
        ProviderType.MOOMOO: 4,
        ProviderType.SCHWAB: 5,
        ProviderType.ETRADE: 6,
    }[ptype]


PROVIDER_NAMES = {
    1: "Robinhood",
    2: "Alpaca",
    3: "Webull",
    4: "Moomoo",
    5: "Schwab",
    6: "E*TRADE",
}


class DuckDBDatastore(BaseDatastore):
    EXPECTED_TABLES: ClassVar[list[str]] = ["providers", "dividends", "symbols", "ticker_holdings"]

    def __init__(self, db_path: str, debug: bool = False):
        super().__init__(duckdb_path=db_path, debug=debug)

    def check_initialized(self) -> bool:
        assert self.executor is not None
        results = self.executor.execute_raw_sql("SHOW ALL TABLES;").fetchall()
        tables = [
            # table_name index is 2; ex: [('data', 'main', 'providers', ['id', 'name'], ['INTEGER', 'VARCHAR'], False)]
            row[2]
            for row in results
        ]
        return set(tables) == set(self.EXPECTED_TABLES)

    def drop(self):
        for x in self.EXPECTED_TABLES:
            self.executor.execute_raw_sql(f"DROP TABLE {x} CASCADE")
        self.executor.connection.commit()

    def intialize_tickers(self, commit: bool = True):
        from py_portfolio_index.bin import STOCK_INFO

        self.executor.execute_raw_sql(
            """
        CREATE OR REPLACE TABLE symbols (
            id INTEGER PRIMARY KEY,
            ticker VARCHAR,
            name VARCHAR,
            sector VARCHAR,
            industry VARCHAR,
            state VARCHAR,
            city VARCHAR,
            country VARCHAR
        );
        """
        )

        final = [
            {
                "id": idx,
                "ticker": v.ticker,
                "name": v.name,
                "sector": v.sector,
                "industry": v.industry,
                "state": v.state if (v.state and v.state != "") else v.location,
                "city": v.location,
                "country": v.country,
            }
            for idx, v in enumerate(STOCK_INFO.values())
        ]
        final += [
            {
                "id": -1,
                "ticker": UNKNOWN_TICKER,
                "name": "Unknown",
                "sector": "Unknown",
                "industry": "Unknown",
                "state": "Unknown",
                "city": "Unknown",
                "country": "Unknown",
            }
        ]
        # one bulk load: row-at-a-time inserts of every known ticker took ~15s
        import pandas as pd

        columns = ["id", "ticker", "name", "sector", "industry", "state", "city", "country"]
        # trilogy types its connection as a protocol; the duckdb handle under it is
        # what can scan a DataFrame
        raw = getattr(getattr(self.executor.connection, "connection", None), "dbapi_connection", None)
        if raw is not None and hasattr(raw, "register"):
            raw.register("_symbols_load", pd.DataFrame(final, columns=columns))
            try:
                raw.execute(f"INSERT INTO symbols ({', '.join(columns)}) SELECT {', '.join(columns)} FROM _symbols_load")
            finally:
                raw.unregister("_symbols_load")
        else:
            from sqlalchemy import text

            self.executor.connection.execute(
                text(f"INSERT INTO symbols ({', '.join(columns)}) VALUES ({', '.join(':' + c for c in columns)})"),
                final,
            )
        if commit:
            self.executor.connection.commit()

    def initialize(self):
        self.executor.execute_raw_sql(
            """
        CREATE OR REPLACE TABLE providers (
            id INTEGER PRIMARY KEY,
            name VARCHAR
        );
        """
        )
        for provider_id, name in PROVIDER_NAMES.items():
            self.executor.execute_raw_sql(
                "INSERT INTO providers VALUES (:id, :name)",
                {"id": provider_id, "name": name},
            )

        self.executor.execute_raw_sql(
            """
        CREATE OR REPLACE TABLE dividends (
            id INTEGER PRIMARY KEY,
            provider INTEGER,
            symbol INTEGER,
            dividend FLOAT,
            dividend_date DATE,
            dividend_external_id VARCHAR,
        );
        """
        )

        self.executor.execute_raw_sql(
            """
        CREATE OR REPLACE TABLE ticker_holdings (
            symbol integer,
            provider integer,
            qty float,
            cost_basis float,
            value float,
            PRIMARY KEY (symbol, provider)
        );
        """
        )
        self.intialize_tickers(commit=False)
        self.executor.connection.commit()

    def persist_dividend_data(self, data: list[DividendResult]):
        from py_portfolio_index.bin import STOCK_INFO

        for x in data:
            # calculate ID as date epoch + symbol + provider for dividends
            self.executor.execute_raw_sql(
                """INSERT INTO dividends 
                                          SELECT 
                                          
                                            FLOOR(epoch(:dividend_date) / 86400) *10000+symbols.id*10+:provider,
                                            :provider,
                                            symbols.id,
                                            :dividend,
                                            :dividend_date,
                                            :external_id
                                          from symbols
                                          where symbols.ticker = :ticker
                ON CONFLICT DO NOTHING;
                                          """,
                {
                    "ticker": x.ticker if x.ticker in STOCK_INFO else UNKNOWN_TICKER,
                    "provider": map_provider(x.provider),
                    "dividend": x.amount.value,
                    "dividend_date": x.date,
                    "external_id": x.external_id,
                },
            )
        self.executor.connection.commit()

    def persist_holding_data(self, data: list[RealPortfolioElement], provider: ProviderType):
        from py_portfolio_index.bin import STOCK_INFO

        mapping = defaultdict(list)
        for x in data:
            mapping[x.ticker.strip().lower()].append(x)
        provider_id = map_provider(provider)
        if provider_id in PROVIDER_NAMES:
            # databases created before a provider was added lack its row
            self.executor.execute_raw_sql(
                "INSERT INTO providers VALUES (:id, :name) ON CONFLICT DO NOTHING;",
                {"id": provider_id, "name": PROVIDER_NAMES[provider_id]},
            )
        # each call is a full snapshot of the provider's holdings: clear the
        # previous one so sold positions drop out and the pooled unknown-ticker
        # row can be written again without a primary key collision
        self.executor.execute_raw_sql(
            "DELETE FROM ticker_holdings WHERE provider = :provider;",
            {"provider": provider_id},
        )
        unknown_qty, unknown_value, unknown_basis = (
            Decimal("0.0"),
            Decimal("0.0"),
            Decimal("0.0"),
        )
        for x in data:
            if x.ticker in STOCK_INFO:
                self.executor.execute_raw_sql(
                    """INSERT INTO ticker_holdings
                                            SELECT 
                                                symbols.id,
                                                :provider,
                                                :qty,
                                                :cost_basis,
                                                :value
                                            from symbols
                                            where symbols.ticker = :ticker
                ON CONFLICT DO UPDATE SET qty = EXCLUDED.qty, cost_basis = EXCLUDED.cost_basis, value = EXCLUDED.value;
                                            """,
                    {
                        "ticker": x.ticker,
                        "provider": provider_id,
                        "qty": x.units,
                        "cost_basis": x.value.decimal - x.appreciation.decimal,
                        "value": x.value.decimal,
                    },
                )
            else:
                unknown_qty += x.units
                unknown_value += x.value.decimal
                unknown_basis += x.value.decimal - x.appreciation.decimal
        if unknown_qty > Decimal("0.0"):
            self.executor.execute_raw_sql(
                """INSERT INTO ticker_holdings
                                        SELECT 
                                            symbols.id,
                                            :provider,
                                            :qty,
                                            :cost_basis,
                                            :value
                                        FROM symbols
                                        WHERE symbols.ticker = :ticker
                """,
                {
                    "ticker": UNKNOWN_TICKER,
                    "provider": provider_id,
                    "qty": unknown_qty,
                    "cost_basis": unknown_basis,
                    "value": unknown_value,
                },
            )

        self.executor.connection.commit()

    def close(self):
        results = self.executor.engine.dispose(close=True)
        self.executor.connection.close()
        import duckdb

        duckdb.default_connection().close()
        import gc

        gc.collect()
        return results
