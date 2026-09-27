# tg-news/rss/rss_import.py

import argparse
import logging

import rss_common as rss


def main(limit):
    sources = rss.db_sources()
    if not sources:
        print("Нет включённых RSS-источников.")
        return

    for source in sources:
        print(f"Импорт: {source['name']}")
        try:
            done = rss.import_feed(source, limit)
        except Exception:
            rss.log.exception("Ошибка импорта источника %s", source["name"])
            continue
        print(f"  импортировано: {done}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Импорт новостей из RSS-лент")
    parser.add_argument("--limit", type=int, default=10, help="новостей на источник")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    main(args.limit)