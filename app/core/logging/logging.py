import logging

from skillcore.logging import configure_logging as configure_logging_core

from .config import LoggingSettings


def configure_logging(settings: LoggingSettings) -> None:
    configure_logging_core(settings, replace_existing_handlers=True)
    _configure_uvicorn_loggers()


def _configure_uvicorn_loggers() -> None:
    for logger_name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.setLevel(logging.NOTSET)
        uvicorn_logger.propagate = True

    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_logger.setLevel(logging.NOTSET)
    access_logger.disabled = True
    access_logger.propagate = False
