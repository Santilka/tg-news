import argparse
import asyncio

import telegram_common as tg


async def main(limit):
    channels = await asyncio.to_thread(tg.db_channels)
    if not channels:
        print("Нет включённых Telegram-каналов.")
        return

    async with tg.make_client() as client:
        for channel in channels:
            print(f"Импорт: {channel['name']}")
            try:
                done = await tg.import_channel(client, channel, limit)
            except Exception:
                tg.log.exception("Ошибка импорта канала %s", channel["name"])
                continue
            print(f"  импортировано/обновлено: {done}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Импорт последних Telegram-публикаций")
    parser.add_argument("--limit", type=int, default=10, help="публикаций на канал")
    args = parser.parse_args()

    tg.setup()
    asyncio.run(main(args.limit))