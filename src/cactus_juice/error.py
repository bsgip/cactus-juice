class BaseJuiceError(Exception):
    """General base exception for anything the juice client might raise"""

    pass


class ConfigError(BaseJuiceError):
    """Something went wrong when trying to configure/start the service"""

    pass


class RequestError(BaseJuiceError):
    """Something went wrong when accessing a remote service"""

    pass
