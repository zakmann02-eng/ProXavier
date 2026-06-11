#!/usr/bin/env python3
"""Entry point for Xavier stock day trading bot."""
import asyncio
import logging
import sys

from xavier.config import XavierConfig
from xavier.bot import XavierBot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("xavier.log"),
    ],
)


def main() -> None:
    try:
        cfg = XavierConfig.load()
    except ValueError as exc:
        print(f"Configuration error: {exc}")
        sys.exit(1)

    bot = XavierBot(cfg)
    asyncio.run(bot.run())


if __name__ == "__main__":
    main()
