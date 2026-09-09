import asyncio
import logging
import signal
from datetime import UTC, datetime, timedelta

from cactus_juice.crud import fetch_csipaus_config
from cactus_juice.csipaus.client import ClientState, run_polls, run_responses
from cactus_juice.csipaus.config import build_csipaus_context
from cactus_juice.db import DatabaseConnection
from cactus_juice.settings import CactusJuiceSettings

logger = logging.getLogger(__name__)

# How often run_responses (and the "is the config still current" check) is run. This is deliberately decoupled
# from the poll/post cadence reported by run_polls - responses should go out promptly regardless of how far away
# the next poll/post is.
RESPONSE_INTERVAL = timedelta(seconds=10)

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


async def _refresh_state(
    db: DatabaseConnection, state: ClientState | None, active_config_id: int | None
) -> tuple[ClientState | None, int | None]:
    """Checks the DB for the currently active CSIPAusConfig - builds and returns a new ClientState if its PK has
    changed since the last check (closing out the previous state's HTTP connection first), otherwise returns
    state/active_config_id unchanged.

    The CSIPAusConfig ORM instance is only ever touched from within its own session block - nothing from it
    escapes past this function, so there's nothing left that could raise once that session has closed."""

    async with db.session_maker() as session:
        config = await fetch_csipaus_config(session)
        if config is None:
            logger.warning("No CSIPAusConfig is currently registered - will check again shortly.")
            return state, active_config_id

        if state is not None and config.csipaus_config_id == active_config_id:
            return state, active_config_id

        new_config_id = config.csipaus_config_id
        logger.info(f"Building new ClientState for CSIPAusConfig id={new_config_id}")
        context = build_csipaus_context(config)

    if state is not None:
        await state.context.http.session.close()

    return ClientState.new_instance(context, db), new_config_id


async def run_csipaus_client_task(settings: CactusJuiceSettings) -> None:
    """Continuously runs the CSIP-Aus client against whatever CSIPAusConfig is currently the active one.

    Every ~RESPONSE_INTERVAL this will:
      * Check the DB for the active CSIPAusConfig - tearing down and rebuilding the ClientState (and its
        underlying HTTP connection) whenever the config's PK has changed.
      * Send any outstanding Responses to the server (run_responses).
      * Run whatever polls/posts are currently due (run_polls) - which itself reports back when it next needs
        to be called, so it isn't re-run on every tick.

    This is intended to run forever as a worker task, until either ctrl-c (SIGINT) or a shutdown request
    (SIGTERM) is received. All other exceptions are caught, logged and then the loop just continues after the
    usual delay - a bad tick should never bring the whole task down."""

    db = DatabaseConnection.new_instance(settings)
    state: ClientState | None = None
    active_config_id: int | None = None
    next_poll_at = datetime.now(UTC)  # Ensure the very first tick always polls

    stop_event, registered_signals = _install_shutdown_handlers(asyncio.get_running_loop())

    try:
        while not stop_event.is_set():
            try:
                prior_state = state
                state, active_config_id = await _refresh_state(db, state, active_config_id)
                if state is not prior_state:
                    next_poll_at = datetime.now(UTC)  # Force an immediate poll against the new state

                if state is not None:
                    await run_responses(state)

                    if datetime.now(UTC) >= next_poll_at:
                        next_poll_at = await run_polls(state)
            except Exception:
                logger.exception("Unhandled exception in CSIP-Aus client task - will retry shortly.")

            # Sleep for RESPONSE_INTERVAL, but wake immediately (rather than up to RESPONSE_INTERVAL late) if a
            # shutdown has been requested in the meantime.
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=RESPONSE_INTERVAL.total_seconds())
            except TimeoutError:
                pass
    finally:
        for sig in registered_signals:
            asyncio.get_running_loop().remove_signal_handler(sig)
        if state is not None:
            await state.context.http.session.close()
        await db.engine.dispose()
        logger.info("CSIP-Aus client task has shut down.")
