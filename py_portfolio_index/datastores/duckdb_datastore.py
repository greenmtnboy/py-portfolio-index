from py_portfolio_index.datastores.base_datastore import BaseDatastore
from py_portfolio_index.models import DividendResult, RealPortfolioElement
from py_portfolio_index.enums import ProviderType
import hashlib


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
    }[ptype]


class DuckDBDatastore(BaseDatastore):
    EXPECTED_TABLES = ["providers", "dividends", "symbols", "ticker_holdings"]

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

    def initialize(self):
        from py_portfolio_index.bin import STOCK_INFO

        self.executor.execute_raw_sql(
            """
        CREATE OR REPLACE TABLE providers (
            id INTEGER PRIMARY KEY,
            name VARCHAR
        );
        """
        )
        self.executor.execute_raw_sql("INSERT INTO providers VALUES (1, 'Robinhood')")
        self.executor.execute_raw_sql("INSERT INTO providers VALUES (2, 'Alpaca')")
        self.executor.execute_raw_sql("INSERT INTO providers VALUES (3, 'Webull')")
        self.executor.execute_raw_sql("INSERT INTO providers VALUES (4, 'Moomoo')")
        self.executor.execute_raw_sql("INSERT INTO providers VALUES (5, 'Schwab')")

        self.executor.execute_raw_sql(
            """
        CREATE OR REPLACE TABLE dividends (
            id INTEGER PRIMARY KEY,
            provider INTEGER,
            symbol INTEGER,
            dividend FLOAT,
            dividend_date DATE
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
        self.executor.execute_raw_sql(
            """
        
        DROP TABLE IF EXISTS symbols CASCADE;
        CREATE TABLE symbols (
            id INTEGER PRIMARY KEY,
            ticker VARCHAR,
            name VARCHAR,
            sector VARCHAR,
            industry VARCHAR,
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
            }
            # (idx+1, v.ticker, v.name)
            for idx, v in enumerate(STOCK_INFO.values())
        ]
        for x in final:
            self.executor.execute_raw_sql(
                "INSERT INTO symbols VALUES (:id, :ticker, :name, :sector, :industry)",
                x,
            )
        self.executor.connection.commit()

    def persist_dividend_data(self, data: list[DividendResult]):
        for x in data:
            # calculate ID as date epoch + symbol + provider for dividends
            self.executor.execute_raw_sql(
                """INSERT INTO dividends 
                                          SELECT 
                                          
                                            FLOOR(epoch(:dividend_date) / 86400) *10000+symbols.id*10+:provider,
                                            :provider,
                                            symbols.id,
                                            :dividend,
                                            :dividend_date
                                          from symbols
                                          where symbols.ticker = :ticker
                ON CONFLICT DO NOTHING;
                                          """,
                {
                    "ticker": x.ticker,
                    "provider": map_provider(x.provider),
                    "dividend": x.amount.value,
                    "dividend_date": x.date,
                },
            )
        self.executor.connection.commit()

    def persist_holding_data(
        self, data: list[RealPortfolioElement], provider: ProviderType
    ):
        for x in data:
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
                    "provider": map_provider(provider),
                    "qty": x.units,
                    "cost_basis": x.value.decimal - x.appreciation.decimal,
                    "value": x.value.value,
                },
            )
        self.executor.connection.commit()
