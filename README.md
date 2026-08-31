# Python Template

This is a template repository for Python projects at BSGIP. It contains a set if configuration files and a suggested directory layout.

## Getting Started

### Install
```
# For dev
uv sync --python 3.13 --all-extras

# For use in other projects (if it's a library dependency)
uv add my-app
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

In order to get all the tools to work well together the chosen line length must be consistent. You will see
a value of 120 appearing in multiple places in `pyproject.toml`, `setup.cfg` and also in the editor settings, for example, settings.json for vscode.

We also recommend using [mypy](http://www.mypy-lang.org/). The `pyproject.toml` contains a default configuration and mypy can be enabled in your editors settings (see below)

## .gitignore

The .gitignore is based off [Github standard python .gitignore](https://github.com/github/gitignore/blob/main/Python.gitignore) with a few extra exclusions for files associated with code editors and operating systems.

## Directory structure

We recommend putting your python app/package code in a `src` directory and putting all tests in a `test` directory. This [article](https://blog.ionelmc.ro/2014/05/25/python-packaging/#the-structure) explains some of the benefits to arrange your repository this way.

If you don't want to use the `src` directory, then you will need to change the following settings:

| Config File | Setting |
| --- | --- |
| `setup.cfg` | `package_dir` setting under `[options] |
| `setup.cfg` | `where` setting under `[options.packages.find]` |
| `pyproject.toml` | `pythonpath` setting under `[tool.pytest.ini_options]` |

## `setup.cfg` and `pyproject.toml`

See the discussion here about the merits of [pyproject.toml vs setup.cfg vs setup.py](https://towardsdatascience.com/setuptools-python-571e7d5500f2).

## Editors

This template repository contains example configuration files for the most popular code editors at BSGIP: [pycharm](https://www.jetbrains.com/pycharm/), [vscode](https://code.visualstudio.com/) and [neovim](https://neovim.io/).

### vscode

The file `vscode/settings.json` is an example configuration for vscode. To use these setting copy this file to `.vscode/settings.json`

The main features of this settings file are:
- Leveraging the uv `.venv` directory as the default for `python.defaultInterpreterPath`
- Autoformat on save (using the ruff formatter)

Consider installing the following extensions:
- ruff - https://marketplace.visualstudio.com/items?itemName=charliermarsh.ruff
- ty - https://marketplace.visualstudio.com/items?itemName=astral-sh.ty

### pycharm
pycharm is currently not used by anyone at BSGIP, and is not currently supported. 

### neovim
Configurations (or 'distributions') are available for neovim. Currently used distributions are:
- [kickstart](https://github.com/nvim-lua/kickstart.nvim) - [tutorial](https://www.youtube.com/watch?v=m8C0Cq9Uv9o)
- [lazyvim](https://www.lazyvim.org/) - [tutorial](https://www.youtube.com/watch?v=N93cTbtLCIM)
