# Plugfest PC specific setup instructions

## Install Dependencies

As root:
```

apt update
apt install postgresql-18 nginx git curl vim
```

## Setup CACTUS Account

As root:
```
groupadd cactus
useradd --create-home --home-dir "/localhome/cactus" --gid cactus --shell /bin/bash cactus
```

As cactus:
```
cd ~/
curl -LsSf https://astral.sh/uv/install.sh | sh

git clone https://github.com/bsgip/cactus-juice.git
git clone https://github.com/bsgip/cactus-orchestrator.git

cd ~/cactus-orchestrator/
git pull
git switch plugfest
```

## Setup DB

### Create DB

As postgres:
```
sudo -u postgres psql

# Create CACTUS-JUICE db
postgres=# create database cactusjuice;
postgres=# create user cactusjuiceuser with encrypted password 'mypass';
postgres=# grant all privileges on database cactusjuice to cactusjuiceuser;
postgres=# alter database cactusjuice owner to cactusjuiceuser;

# Create CACTUS-ORCHESTRATOR db
postgres=# create database cactusorchestrator;
postgres=# create user cactususer with encrypted password 'mypass';
postgres=# grant all privileges on database cactusorchestrator to cactususer;
postgres=# alter database cactusorchestrator owner to cactususer;
```

### Apply schema migrations

As cactus:
```
cd ~/cactus-juice/
echo 'export JUICE_DATABASE_URL="postgresql+asyncpg://cactusjuiceuser:mypass@localhost:5432/cactusjuice"' > .env
uv sync --all-extras
uv run dotenv run alembic upgrade head

cd ~/cactus-orchestrator/
echo 'export ORCHESTRATOR_DATABASE_URL="postgresql+asyncpg://cactususer:mypass@localhost:5432/cactusorchestrator"' > .env
echo 'export FILE_STORE_PATH="/var/lib/cactus/filestore"' >> .env
echo 'export CACTUS_FQDN="https://localhost"' >> .env
uv sync --all-extras
uv run dotenv run alembic upgrade head
```


## Do the normal CACTUS setup.sh