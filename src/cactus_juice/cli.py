import asyncio
import logging
import sys

import click

from cactus_juice.settings import CactusJuiceSettings
from cactus_juice.tasks import TASKS

logger = logging.getLogger(__name__)


def configure_logging() -> None:
    """Ensures python logging emits to stdout - this is what a container runtime will typically capture."""
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@click.group()
def main() -> None:
    """cactus-juice - run either the backend API or one of its continuously running worker tasks."""
    configure_logging()


@main.command("task")
@click.argument("name", type=click.Choice(sorted(TASKS)))
def task(name: str) -> None:
    """Run a worker task continuously (until interrupted). Intended for containerised deployments where the
    backend image is run in "worker mode" rather than as the FastAPI app."""

    settings = CactusJuiceSettings()  # ty: ignore[missing-argument]  # values are sourced from the environment
    logger.info(f"Starting task '{name}'")
    try:
        asyncio.run(TASKS[name](settings))
    except KeyboardInterrupt:
        logger.info(f"Task '{name}' interrupted - shutting down.")


if __name__ == "__main__":
    main()
