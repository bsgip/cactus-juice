import asyncio
import logging
import signal
from datetime import UTC, datetime, timedelta

from cactus_juice.crud import fetch_troca_config, upsert_task_health
from cactus_juice.db import DatabaseConnection
from cactus_juice.settings import CactusJuiceSettings
from cactus_juice.troca.client import TrocaClient
from cactus_juice.troca.poll import ClientState, run_polls

logger = logging.getLogger(__name__)

# Matches this task's key in cactus_juice.tasks.TASKS - used as the TaskHealth row's task_name.
TASK_NAME = "trocaclient"

# How long to wait before checking again when there's no usable TrocaConfig on record yet (either none exists,
# or it's missing a connector_id) - there's no poll schedule to speak of in that state, so this is just a fixed
# retry cadence rather than anything derived from run_polls.
CONFIG_RETRY_INTERVAL = timedelta(seconds=10)

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
    """Checks the DB for the currently active TrocaConfig - builds and returns a new ClientState if its PK has
    changed since the last check (closing out the previous state's TrocaClient first), otherwise returns
    state/active_config_id unchanged.

    TrocaConfig is rolling-history/insert-only (see crud.update_troca_config) - a config being changed always
    means a new row (and PK) is inserted, so tracking the PK is equivalent to tracking when the record last
    changed.

    The TrocaConfig ORM instance is only ever touched from within its own session block - nothing from it
    escapes past this function, so there's nothing left that could raise once that session has closed."""

    async with db.session_maker() as session:
        config = await fetch_troca_config(session)
        if config is None:
            logger.warning("No TrocaConfig is currently registered - will check again shortly.")
            return state, active_config_id

        if config.connector_id is None:
            logger.warning("TrocaConfig has no connector_id configured yet - will check again shortly.")
            return state, active_config_id

        if state is not None and config.troca_config_id == active_config_id:
            return state, active_config_id

        new_config_id = config.troca_config_id
        logger.info(f"Building new ClientState for TrocaConfig id={new_config_id}")
        client = TrocaClient(config.base_url, config.basic_user, config.basic_password)
        new_state = ClientState.new_instance(
            client,
            db,
            config.connector_id,
            config.reading_poll_rate_seconds,
            config.schedule_poll_rate_seconds,
            config.metadata_poll_rate_seconds,
            config.ramp_step_seconds,
        )

    if state is not None:
        await state.client.close()

    return new_state, new_config_id


async def _record_health(db: DatabaseConnection, exception: BaseException | None) -> None:
    """Upserts this task's TaskHealth row to reflect a tick just having run (and, if exception is given, that
    it failed) - failures here are logged and swallowed so a health-tracking hiccup never masks the tick's own
    error handling."""

    try:
        async with db.session_maker() as session:
            await upsert_task_health(
                session, TASK_NAME, datetime.now(UTC), str(exception) if exception is not None else None
            )
            await session.commit()
    except Exception:
        logger.exception(f"Failed to record task health for '{TASK_NAME}'.")


async def run_troca_client_task(settings: CactusJuiceSettings) -> None:
    """Continuously runs the Troca client against whatever TrocaConfig is currently the active one.

    Every tick this will:
      * Check the DB for the active TrocaConfig - tearing down and rebuilding the ClientState (and its
        underlying TrocaClient) whenever the config's PK has changed.
      * Run whatever polls/pushes are currently due (run_polls) - which itself reports back when it next needs
        to be called, so it isn't re-run on every tick.

    This is intended to run forever as a worker task, until either ctrl-c (SIGINT) or a shutdown request
    (SIGTERM) is received. All other exceptions are caught, logged and then the loop just continues after the
    usual delay - a bad tick should never bring the whole task down."""

    db = DatabaseConnection.new_instance(settings)
    state: ClientState | None = None
    active_config_id: int | None = None
    next_poll_at = datetime.now(UTC)  # Ensure the very first tick always polls (once a config is available)

    stop_event, registered_signals = _install_shutdown_handlers(asyncio.get_running_loop())

    try:
        while not stop_event.is_set():
            try:
                prior_state = state
                state, active_config_id = await _refresh_state(db, state, active_config_id)
                if state is not prior_state:
                    next_poll_at = datetime.now(UTC)  # Force an immediate poll against the new state

                if state is not None and datetime.now(UTC) >= next_poll_at:
                    next_poll_at = await run_polls(state)
            except Exception as exc:
                logger.exception("Unhandled exception in Troca client task - will retry shortly.")
                await _record_health(db, exc)
            else:
                await _record_health(db, None)

            # Sleep until the next poll is due (or CONFIG_RETRY_INTERVAL if there's no state yet), but wake
            # immediately (rather than up to that long late) if a shutdown has been requested in the meantime.
            if state is None:
                wait_seconds = CONFIG_RETRY_INTERVAL.total_seconds()
            else:
                wait_seconds = max((next_poll_at - datetime.now(UTC)).total_seconds(), 0)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=wait_seconds)
            except TimeoutError:
                pass
    finally:
        for sig in registered_signals:
            asyncio.get_running_loop().remove_signal_handler(sig)
        if state is not None:
            await state.client.close()
        await db.engine.dispose()
        logger.info("Troca client task has shut down.")
