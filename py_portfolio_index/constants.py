from logging import Logger as baseLogger
from logging import getLogger

LOGGER_NAME = "py_portfolio_index"

CACHE_DIR = "py_portfolio_index"

AUTO_TARGET_SIZE = -1

Logger: baseLogger = getLogger(LOGGER_NAME)

UNKNOWN_TICKER = "???"
