"""Centralized logging setup."""

import logging
import os
import sys
from logging.handlers import WatchedFileHandler
from typing import Optional
from contextvars import ContextVar


request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def _handler_exists(
    logger: logging.Logger, handler_type: type, *, filename: Optional[str] = None
) -> bool:
    for handler in logger.handlers:
        if isinstance(handler, handler_type):
            if filename is None:
                return True
            base_filename = getattr(handler, "baseFilename", None)
            if base_filename == filename:
                return True
    return False


def configure_logging(*, environment: str, log_level: str) -> int:
    level = getattr(logging, (log_level or "INFO").upper(), logging.INFO)

    logger = logging.getLogger()
    logger.setLevel(level)

    formatter = logging.Formatter(
        "[%(asctime)s.%(msecs)03d] [%(levelname)-5s] [%(name)-20s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if not _handler_exists(logger, logging.StreamHandler):
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    app_log_path = os.getenv("APP_LOG_PATH", "").strip()
    if app_log_path:
        try:
            log_dir = os.path.dirname(app_log_path)
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)
            if not _handler_exists(logger, WatchedFileHandler, filename=app_log_path):
                file_handler = WatchedFileHandler(app_log_path)
                file_handler.setLevel(level)
                file_handler.setFormatter(formatter)
                logger.addHandler(file_handler)
        except OSError as exc:
            logger.warning(
                "Failed to configure APP_LOG_PATH logging for %s: %s",
                app_log_path,
                exc,
            )

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True
        uv_logger.setLevel(level)

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    return level
