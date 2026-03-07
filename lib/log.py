"""Shared logging setup — dual handler (console + file)."""

import logging
import pathlib

from lib.config import LOG_DIR


def setup_logger(name: str, log_file: str) -> logging.Logger:
    """Create a logger with console (INFO) + file (DEBUG) handlers.

    Returns the same logger if already configured (idempotent).
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)

    if logger.handlers:
        return logger  # already configured

    logger.setLevel(logging.DEBUG)

    # Console — emoji-friendly, minimal
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(ch)

    # File — timestamped, verbose
    fh = logging.FileHandler(LOG_DIR / log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(fh)

    return logger
