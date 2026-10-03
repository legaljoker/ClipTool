"""Logging: everything goes to logs/cliptool.log, key steps also to the screen."""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path
from typing import Optional

LOGGER_NAME = "cliptool"
_configured = False


def setup_logging(log_dir: Path, verbose: bool = False) -> logging.Logger:
    global _configured
    logger = logging.getLogger(LOGGER_NAME)
    if _configured:
        return logger
    logger.setLevel(logging.DEBUG)

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(console)

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_dir / "cliptool.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        logger.addHandler(fh)
    except OSError as exc:  # read-only folder etc. - keep going with console only
        logger.warning("Could not open log file in %s: %s", log_dir, exc)

    _configured = True
    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)


def add_job_log(job_dir: Path) -> logging.Handler:
    """Also write a copy of the log into the job's output folder."""
    handler = logging.FileHandler(job_dir / "job.log", encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logging.getLogger(LOGGER_NAME).addHandler(handler)
    return handler


def remove_handler(handler: logging.Handler) -> None:
    logging.getLogger(LOGGER_NAME).removeHandler(handler)
    handler.close()
