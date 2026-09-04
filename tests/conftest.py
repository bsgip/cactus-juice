import os
import subprocess
from collections.abc import Generator
from pathlib import Path

import psycopg
import pytest
from alembic.config import Config
from assertical.fixtures.environment import environment_snapshot
from psycopg import Connection
from pytest_postgresql.executors import PostgreSQLExecutor
from pytest_postgresql.janitor import DatabaseJanitor

from alembic import command

# Name of the throwaway database used (once per test session) to run the full alembic migration
# chain against so its resulting schema/data can be dumped for pg_migrated_schema_dump
MIGRATED_SCHEMA_DB_NAME = "juice_test_migrated_schema"

ALEMBIC_INI = Path(__file__).parent.parent / "alembic.ini"


def execute_test_sql(cfg: Connection, sql: str) -> None:
    with cfg.cursor() as cursor:
        cursor.execute(sql)  # ty:ignore[no-matching-overload]
        cfg.commit()


def build_urls(conn: Connection) -> tuple[str, str]:
    """Return (sync_url, async_url) from a psycopg2 connection."""
    p = conn.info
    host = p.host or "localhost"
    port = p.port
    user = p.user
    dbname = p.dbname
    password = p.password or ""
    credentials = f"{user}:{password}@" if password else f"{user}@"
    base = f"{credentials}{host}:{port}/{dbname}"
    return f"postgresql://{base}", f"postgresql+asyncpg://{base}"


@pytest.fixture(autouse=True)
def anyio_backend():
    return "asyncio"


@pytest.fixture
def preserved_environment():
    with environment_snapshot():
        yield


@pytest.fixture(scope="session")
def pg_migrated_schema_dump(postgresql_proc: PostgreSQLExecutor) -> Generator[str]:
    """Runs ONCE for the entire test session.

    Creates a dedicated (throwaway) database on the shared postgres instance, runs the full chain
    of alembic migrations against it (via upgrade()) and exports the resulting schema - plus any
    data seeded by the migrations themselves (e.g. default SiteControlGroup/SiteDER rows) - as a
    plain SQL dump via pg_dump.

    pg_empty_config applies this dump directly to each test's (already empty) database rather
    than re-running the full alembic migration chain for every single test - this is a LOT
    quicker as alembic has to plan/execute dozens of migrations individually whereas applying a
    flat SQL dump is comparatively instant.
    """

    janitor = DatabaseJanitor(
        user=postgresql_proc.user,
        host=postgresql_proc.host,
        port=postgresql_proc.port,
        dbname=MIGRATED_SCHEMA_DB_NAME,
        password=postgresql_proc.password,
    )
    janitor.init()
    try:
        with environment_snapshot():
            migration_conn = psycopg.connect(
                dbname=MIGRATED_SCHEMA_DB_NAME,
                user=postgresql_proc.user,
                password=postgresql_proc.password,
                host=postgresql_proc.host,
                port=postgresql_proc.port,
            )
            _, async_url = build_urls(migration_conn)

            try:
                # This will install all of the alembic migrations. env.py drives them through an
                # async engine, so it needs the +asyncpg URL (not the plain sync one).
                cfg = Config(str(ALEMBIC_INI))
                cfg.set_main_option("sqlalchemy.url", async_url)
                command.upgrade(cfg, "head")
            finally:
                migration_conn.close()

        pg_dump_result = subprocess.run(
            [
                "pg_dump",
                "--inserts",  # Emit data as INSERT statements (instead of COPY) so it can be replayed via psycopg
                "--no-owner",
                "--no-privileges",
                "-h",
                str(postgresql_proc.host),
                "-p",
                str(postgresql_proc.port),
                "-U",
                postgresql_proc.user,
                "-d",
                MIGRATED_SCHEMA_DB_NAME,
            ],
            env={**os.environ, "PGPASSWORD": postgresql_proc.password or ""},
            capture_output=True,
            text=True,
            check=True,
        )
    finally:
        janitor.drop()

    # pg_dump (PG 18+) wraps its output in psql-only "\restrict"/"\unrestrict" meta-commands that
    # aren't valid SQL and break execution via psycopg - strip them out, they only guard against
    # psql executing arbitrary functions mid-restore which isn't a concern for this test dump.
    dump_sql = "\n".join(
        line
        for line in pg_dump_result.stdout.splitlines()
        if not line.startswith("\\restrict") and not line.startswith("\\unrestrict")
    )

    yield dump_sql


@pytest.fixture
def pg_empty_config(postgresql, preserved_environment, pg_migrated_schema_dump: str) -> Generator[Connection]:
    """Sets up the testing DB, applies migrations but does NOT add any entities"""

    # Rather than re-running the full (slow) alembic migration chain against this test's database,
    # apply the schema/data dump exported once per session by pg_migrated_schema_dump - this is
    # functionally equivalent to calling upgrade() but a lot quicker.
    execute_test_sql(postgresql, pg_migrated_schema_dump)

    # pg_dump's preamble resets this connection's search_path to '' (it fully schema-qualifies
    # everything it emits so it doesn't need one) - restore the normal default so any unqualified
    # SQL run against this connection for the rest of the test resolves as expected.
    execute_test_sql(postgresql, "SET search_path TO public")

    yield postgresql


@pytest.fixture
def pg_base_config(pg_empty_config):
    """Adds a very minimal config to the database from base_config.sql"""
    with open("tests/data/sql/base_config.sql") as f:
        sql = f.read()

    execute_test_sql(pg_empty_config, sql)
    yield pg_empty_config
