from logging import getLogger, Logger as baseLogger

LOGGER_NAME = "py_portfolio_index"

CACHE_DIR = "py_portfolio_index"

AUTO_TARGET_SIZE = -1

Logger: baseLogger = getLogger(LOGGER_NAME)
