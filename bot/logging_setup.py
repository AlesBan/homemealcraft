from __future__ import annotations

import logging
import sys


def setup_logging(level: str = "INFO") -> None:
    """Compact logs: our app informative, noisy libraries quiet."""
    root_level = getattr(logging, level.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(root_level)

    # App loggers
    logging.getLogger("homemealcraft").setLevel(root_level)

    # Quiet chatty deps
    for name in (
        "aiogram.event",
        "aiogram.dispatcher",
        "aiohttp",
        "asyncio",
        "sqlalchemy.engine",
        "sqlalchemy.pool",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)

    # Keep start/stop of polling visible at INFO for our logger only
    logging.getLogger("aiogram.dispatcher").setLevel(logging.WARNING)
