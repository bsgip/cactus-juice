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

As root:
```

vim /etc/postgresql/18/main/postgresql.conf
# Set listen_addresses = '*';

vim /etc/postgresql/18/main/pg_hba.conf
# Set the following (double check the IP ranges for the podman network gateways that are created later)
# host    cactusjuice    cactusjuiceuser    10.89.100.0/24    scram-sha-256
# host    cactusorchestrator cactususer    10.89.0.1/24       scram-sha-256


```

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


## Nginx
As root:
```
cd /localhome/cactus/cactus-juice/deploy/server

# Create nginx config
./nginx-config.sh der cactus.env   > /etc/nginx/sites-available/etc.cactus.localhost
./nginx-config.sh webui cactus.env > /etc/nginx/sites-available/cactus.localhost
./nginx-config.sh juice cactus.env > /etc/nginx/sites-available/juice.localhost
./nginx-config.sh http cactus.env  > /etc/nginx/nginx.conf

# Link it to sites-enabled
ln -s /etc/nginx/sites-available/etc.cactus.localhost /etc/nginx/sites-enabled/etc.cactus.localhost
ln -s /etc/nginx/sites-available/cactus.localhost /etc/nginx/sites-enabled/cactus.localhost
ln -s /etc/nginx/sites-available/juice.localhost /etc/nginx/sites-enabled/juice.localhost
```

## Build orchestrator image

As cactus:
```
cd ~/cactus-juice
podman build -t cactus-orchestrator:plugfest_latest --build-arg CACTUS_ORCHESTRATOR_VERSION="plugfest" --build-arg GITHUB_ORG="bsgip" "./deploy/docker/cactus-orchestrator" --no-cache
```





## Start services
As cactus:
```
cd ~/cactus-juice/deploy/server
./update.sh
./update-juice.sh
```

## Updating Orchestrator Test Definitions

**Only required when changing test definitions**

### Change Test Definitions
**On your dev machine**

* Update [cactus-test-definitions](https://github.com/bsgip/cactus-test-definitions) 
* Use the `plugfest_definitions` branch
* No tagging required - just push to the head of the `plugfest_definitions` branch

### Update Orchestrator Repo
**On your dev machine**
* Update [cactus-orchestrator](https://github.com/bsgip/cactus-orchestrator)
* Use the `plugfest` branch
* The repo is already having `pyproject.toml` reference the above `cactus-test-definitions` branch - you just need to update the `uv.lock` file
* `uv lock --upgrade-package cactus-test-definitions`
* The `uv.lock` should be updated to the latest commit ID in `cactus-test-definitions`
* Commit and push the `cactus-orchestrator` changes (should just be `uv.lock`)

### Deploy Changes
**On plugfest-pc**

```
cd ~/cactus-juice
podman build -t cactus-orchestrator:plugfest_latest --build-arg CACTUS_ORCHESTRATOR_VERSION="plugfest" --build-arg GITHUB_ORG="bsgip" "./deploy/docker/cactus-orchestrator" --no-cache

cd ~/cactus-juice/deploy/server
./update.sh
```