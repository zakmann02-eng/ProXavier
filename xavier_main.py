"""ProXavier — entry point."""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from xavier.config import XavierConfig
from xavier.bot import XavierBot


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )


def main() -> None:
    _setup_logging()
    cfg = XavierConfig.from_env()
    bot = XavierBot(cfg)
    asyncio.run(bot.run())


if __name__ == "__main__":
    main()
