from datetime import datetime
from typing import overload


class BaseJuiceError(Exception):
    """General base exception for anything the juice client might raise"""

    pass


class ConfigError(BaseJuiceError):
    """Something went wrong when trying to configure/start the service"""

    pass


class RequestError(BaseJuiceError):
    """Something went wrong when accessing a remote service (eg HTTP 500)"""

    status_code: int | None  # HTTP status code received (or None if this is a connection error)

    @overload
    def __init__(self, message: str) -> None: ...
    @overload
    def __init__(self, message: str, status_code: int) -> None: ...
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RemoteServiceError(BaseJuiceError):
    """There is an error in the remote service (perhaps it is returning invalid data)"""

    pass


class PollError(BaseJuiceError):
    """One or more polls in a run_polls call failed (the remaining polls in that call still ran).

    The message summarises every failure (so it can be surfaced via TaskHealth) and next_poll_at is the wakeup
    time run_polls would otherwise have returned - so callers can keep to the poll schedule despite the failure."""

    failures: list[tuple[str, Exception]]  # (description of what failed, the exception raised)
    next_poll_at: datetime

    def __init__(self, failures: list[tuple[str, Exception]], next_poll_at: datetime) -> None:
        super().__init__("; ".join(f"{label}: {str(exc) or type(exc).__name__}" for label, exc in failures))
        self.failures = failures
        self.next_poll_at = next_poll_at
