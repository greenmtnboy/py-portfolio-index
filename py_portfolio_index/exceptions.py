class PriceFetchError(Exception):
    pass


class ConfigurationError(Exception):
    pass


class OrderError(Exception):

    def __init__(self, message, *args):
        super().__init__(message, *args)
        self.message = message