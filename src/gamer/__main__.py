"""CLI entrypoint. ``python -m gamer`` or the ``gamer`` console script."""

from __future__ import annotations

import asyncio

from gamer.app import main_async


def main() -> None:
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
