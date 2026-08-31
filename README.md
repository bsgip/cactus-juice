# CACTUS Juice

Demonstration project showing how to link a CSIP-Aus utility server to OCPP for the purposes of compliance testing. This is NOT a true CSIP-Aus/OCPP client - it exists to showcase how the two can be linked.

## Getting Started

### Install
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

## Standard Python Tools

These are the standard python tools that should be used on any new Python projects.

| Name | Configuration file | Purpose | URL |
| --- | --- | --- | --- |
| bandit | pyproject.toml | Checks your code for security issues | https://github.com/PyCQA/bandit |
| ruff | pyproject.toml | Formatter / Linter | https://github.com/astral-sh/ruff |
| ty | pyproject.toml | Typechecker | https://github.com/astral-sh/ty |
| codespell | - | Spellchecker | https://pypi.org/project/codespell/ |
| coverage | - |  Test coverage metric | https://coverage.readthedocs.io/en |
| pytest | pytest.ini | Testing framework | https://docs.pytest.org/ |

Some tools have a configuration settings. The table above indicates in which configuration file to look for settings for that tool.

