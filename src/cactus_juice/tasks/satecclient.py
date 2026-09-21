import asyncio
import logging
import signal
from datetime import UTC, datetime

from cactus_juice.db import DatabaseConnection
from cactus_juice.satec.client import ClientState, close, run_polls
from cactus_juice.settings import CactusJuiceSettings

logger = logging.getLogger(__name__)

# SIGINT is ctrl-c, SIGTERM is what docker/k8s/most process supervisors send to ask a process to shut down
# cleanly before force-killing it - both should cause the task loop to exit gracefully rather than SIGTERM
# just killing the process mid-iteration with no cleanup.
SHUTDOWN_SIGNALS = (signal.SIGINT, signal.SIGTERM)


def _install_shutdown_handlers(loop: asyncio.AbstractEventLoop) -> tuple[asyncio.Event, list[signal.Signals]]:
    """Registers handlers for SHUTDOWN_SIGNALS that set the returned Event rather than raising/killing the
    process outright. Returns the Event along with whichever signals were successfully registered (so they can
    be torn down again) - a platform that doesn't support add_signal_handler (eg Windows) is logged and skipped,
    falling back to default signal handling for that signal."""

    stop_event = asyncio.Event()
    registered: list[signal.Signals] = []
    for sig in SHUTDOWN_SIGNALS:
        try:
            loop.add_signal_handler(sig, stop_event.set)
            registered.append(sig)
        except (NotImplementedError, RuntimeError):
            logger.warning(f"Unable to install a shutdown handler for {sig!r} on this platform/thread.")
    return stop_event, registered


async def run_satec_client_task(settings: CactusJuiceSettings) -> None:
    """Continuously polls every registered SatecConfig meter, writing samples into SatecReading.

    Unlike the CSIP-Aus client task, there's no single "active config" here - run_polls itself reconciles its
    ClientState against whatever SatecConfig rows currently exist (added/edited/deleted) on every call, and
    reports back when it next needs to be called, so it isn't re-run on every tick.

    This is intended to run forever as a worker task, until either ctrl-c (SIGINT) or a shutdown request
    (SIGTERM) is received. All other exceptions are caught, logged and then the loop just continues after the
    usual delay - a bad tick should never bring the whole task down."""

    db = DatabaseConnection.new_instance(settings)
    state = ClientState.new_instance(db)
    next_poll_at = datetime.now(UTC)  # Ensure the very first tick always polls

    stop_event, registered_signals = _install_shutdown_handlers(asyncio.get_running_loop())

    try:
        while not stop_event.is_set():
            try:
                if datetime.now(UTC) >= next_poll_at:
                    next_poll_at = await run_polls(state)
            except Exception:
                logger.exception("Unhandled exception in SATEC client task - will retry shortly.")

            # Sleep until the next poll is due, but wake immediately (rather than up to that long late) if a
            # shutdown has been requested in the meantime.
            wait_seconds = max((next_poll_at - datetime.now(UTC)).total_seconds(), 0)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=wait_seconds)
            except TimeoutError:
                pass
    finally:
        for sig in registered_signals:
            asyncio.get_running_loop().remove_signal_handler(sig)
        await close(state)
        await db.engine.dispose()
        logger.info("SATEC client task has shut down.")
