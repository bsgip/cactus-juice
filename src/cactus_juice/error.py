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
