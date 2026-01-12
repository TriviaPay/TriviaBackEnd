"""Centralized logging setup."""

import logging
import sys
from contextvars import ContextVar


request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def configure_logging(*, environment: str, log_level: str) -> int:
    level = getattr(logging, (log_level or "INFO").upper(), logging.INFO)

    logger = logging.getLogger()
    logger.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter(
            "[%(asctime)s.%(msecs)03d] [%(levelname)-5s] [%(name)-20s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    if not logger.handlers:
        logger.addHandler(handler)

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    logging.getLogger("uvicorn").setLevel(level)
    logging.getLogger("uvicorn.error").setLevel(level)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    return level

