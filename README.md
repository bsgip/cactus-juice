# CACTUS Juice

Demonstration project showing how to link a CSIP-Aus utility server to OCPP for the purposes of compliance testing. This is NOT a true CSIP-Aus/OCPP client - it exists to showcase how the two can be linked.

## Environment Variables

| Environment Variable | Default Value | Description |
|----------------------|----------------|-------------|
| `JUICE_DATABASE_URL` | – | SQLAlchemy-style database connection string using `postgresql+asyncpg` scheme. |
| `SECURE_TEMP_DIR_ROOT` | `/dev/shm` | A tempdir is required for loading certs/keys - this is an unavoidable weakness of the python crypto library |

For development - we recommend the use of a local `.env` file - subsequent commands will assume the existence of this file.

## Getting Started

### Install Dependencies
```
# For dev
uv sync --python 3.13 --all-extras
```
### Running tools
```
# Tests
uv run pytest

# Linters
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run bandit -c pyproject.toml -r src/
```
### Database

Requires a postgres 16+ database

**Create DB**
```
echo 'export JUICE_DATABASE_URL="postgresql+asyncpg://cactusjuiceuser:mypass@localhost:5432/cactusjuice' > .env
sudo -u postgres psql
postgres=# create database cactusjuice;
postgres=# create user cactusjuiceuser with encrypted password 'mypass';
postgres=# grant all privileges on database cactusjuice to cactusjuiceuser;
postgres=# alter database cactusjuice owner to cactusjuiceuser;
```

**Apply Migrations**
```
uv run dotenv run alembic upgrade head
```

**Create new migration**
```
uv run dotenv run alembic revision --autogenerate -m "new_migration"
```

