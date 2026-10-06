import logging
import os
import sys


def setup_logging() -> None:
    """
    Configure application-wide production logging for HireFlo.
    """

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    level = getattr(logging, log_level, logging.INFO)

    logging.basicConfig(
        level=level,
        format=(
            "%(asctime)s | "
            "%(levelname)s | "
            "%(name)s | "
            "%(message)s"
        ),
        handlers=[
            logging.StreamHandler(sys.stdout)
        ],
        force=True,
    )

    # Reduce noisy third-party logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "HireFlo production logging initialized | level=%s",
        log_level,
    )


def get_logger(name: str) -> logging.Logger:
    """
    Return a logger for a HireFlo module.
    """
    return logging.getLogger(name)
