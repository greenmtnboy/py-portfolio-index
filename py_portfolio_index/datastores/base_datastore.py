from typing import Protocol, List, Any
from py_portfolio_index.enums import ObjectKey, ProviderType
from datetime import datetime
from trilogy import Environment, Dialects, Executor
from pathlib import Path
from trilogy.dialect.config import DuckDBConfig
from py_portfolio_index.models import DividendResult, RealPortfolioElement


class ResultProtocol(Protocol):
    values: List[Any]
    columns: List[str]

    def fetchall(self) -> List[Any]: ...

    def keys(self) -> List[str]: ...


class DBApiConnectionWrapper:
    def __init__(self, dbapi):
        self.dbapi = dbapi

    def connect(self):
        return self.dbapi


class BaseDatastore:

    def __init__(self, duckdb_path: str):
        self.duckdb_path = duckdb_path
        self.executor: Executor = self.connect()

        if not self.check_initialized():
            self.initialize()

    def connect(self):
        env = Environment(working_path=Path(__file__).parent)
        self.executor = Dialects.DUCK_DB.default_executor(
            environment=env, conf=DuckDBConfig(path=self.duckdb_path)
        )
        for _ in self.executor.parse_file(Path(__file__).parent / "entrypoint.preql"):
            pass
        return self.executor

    def reset(self):
        self.drop()
        self.connect()
        self.initialize()

    def drop(self):
        raise NotImplementedError

    def check_initialized(self) -> bool:
        raise NotImplementedError

    def initialize(self):
        raise NotImplementedError

    def query(self, query: str) -> ResultProtocol:
        return self.executor.execute_text(query)[-1]

    def get_watermarks(
        self, object_key: ObjectKey, provider_type: ProviderType | None = None
    ) -> tuple[datetime | None, datetime | None]:
        if object_key == ObjectKey.DIVIDENDS:
            query = "dividend.date"
        else:
            raise NotImplementedError
        base_query = f"""
        SELECT
            min({query}) as start,
            max({query}) as end;
        """
        if provider_type:
            base_query = (
                f"WHERE dividend.provider.name='{provider_type.value}' " + base_query
            )
        results = list(self.query(base_query).fetchall())
        if not results:
            return None, None
        return results[0][0], results[0][1]

    def persist_dividend_data(self, data: list[DividendResult]):
        raise NotImplementedError

    def persist_holding_data(
        self, data: list[RealPortfolioElement], provider: ProviderType
    ):
        raise NotImplementedError
