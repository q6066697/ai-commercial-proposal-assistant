"""
Imports data/price_list.json into the database of the API service and makes
it the active version:

    python scripts/import_price_list.py

Safe to run again: an unchanged file changes nothing. A file whose prices or
rules changed under an already imported meta.version is rejected.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db.price_lists import ImportResult, PriceListError, import_price_list, read_price_list_file  # noqa: E402
from db.session import make_engine, make_session_factory  # noqa: E402
from price_list import PRICE_LIST_PATH  # noqa: E402
from settings import get_settings  # noqa: E402

MESSAGES = {
    "created": "импортирован и активен",
    "activated": "уже был в базе, снова активен",
    "unchanged": "уже импортирован и активен, изменений нет",
}


async def run(path: Path) -> ImportResult:
    engine = make_engine(get_settings())
    try:
        async with make_session_factory(engine).begin() as session:
            return await import_price_list(session, read_price_list_file(path))
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="data/price_list.json -> PostgreSQL")
    parser.add_argument("path", nargs="?", type=Path, default=PRICE_LIST_PATH)
    args = parser.parse_args()
    try:
        result = asyncio.run(run(args.path))
    except PriceListError as e:
        sys.exit(str(e))
    print(f"Прайс «{result.version}» ({result.services} услуг): {MESSAGES[result.outcome]}")


if __name__ == "__main__":
    main()
