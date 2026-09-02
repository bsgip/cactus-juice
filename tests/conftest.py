import os
from collections.abc import Generator

import pytest
from assertical.fixtures.environment import environment_snapshot
from assertical.fixtures.postgres import generate_async_conn_str_from_connection
from asyncpg import Connection
from sqlalchemy import NullPool, create_engine

from cactus_juice.model import Base


@pytest.fixture
def preserved_environment():
    with environment_snapshot():
        yield


@pytest.fixture
def pg_empty_config(postgresql, preserved_environment) -> Generator[Connection]:
    """Sets up the testing DB, applies migrations but does NOT add any entities"""

    # Install the JUICE_DATABASE_URL before running migrations
    os.environ["JUICE_DATABASE_URL"] = generate_async_conn_str_from_connection(postgresql)

    sync_conn_string = (
        f"postgresql+psycopg://{postgresql.info.user}:@{postgresql.info.host}:{postgresql.info.port}"
        f"/{postgresql.info.dbname}"
    )
    engine = create_engine(sync_conn_string, echo=False, poolclass=NullPool)
    Base.metadata.create_all(engine)

    yield postgresql

    Base.metadata.drop_all(engine)


@pytest.fixture
def pg_base_config(pg_empty_config):
    """Adds a very minimal config to the database from base_config.sql"""
    execute_test_sql_file(pg_empty_config, "tests/data/base_config.sql")

    yield pg_empty_config


def execute_test_sql_file(cfg, path_to_sql_file: str) -> None:
    with open(path_to_sql_file) as f:
        sql = f.read()
    with cfg.cursor() as cursor:
        cursor.execute(sql)
        cfg.commit()
