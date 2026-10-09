# Plugfest PC specific setup instructions

## Install Dependencies

As root:
```

apt update
apt install postgresql-18 nginx git curl vim podman aardvark-dns netavark catatonit
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

## ENV file

As cactus:
```
cp ~/cactus-juice/deploy/sample.cactus.env ~/cactus-juice/deploy/cactus.env
vim ~/cactus-juice/deploy/cactus.env
```

## PKI

As root:
```
cd ~/cactus-juice/deploy/pki
source ../server/cactus.env
./create-cert.sh device     serca 1 device-chain     1
./create-cert.sh aggregator serca 1 aggregator-chain 2
./create-cert.sh dnsp       serca 1 dnsp-chain       3 envoy 1 "*.${CACTUS_FQDN}"
./stage-certs.sh . ../server/cactus.env
```

## Run setup scripts

As root:
```
cd ~/cactus-juice/deploy/server
./setup.sh
./setup-juice.sh
```

## Build orchestrator image

As cactus:
```
cd ~/cactus-juice
podman build -t cactus-orchestrator:plugfest_latest --build-arg CACTUS_ORCHESTRATOR_VERSION="plugfest" --build-arg GITHUB_ORG="bsgip" "./deploy/docker/cactus-orchestrator"
```

## Start services
As cactus:
```
cd ~/cactus-juice/deploy/server
./update.sh
./update-juice.sh
```
