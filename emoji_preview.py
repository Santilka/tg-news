# emoji_preview.py
# вот пример для скачивания эмодзи python emoji_preview.py 5447607145241550118

import argparse
import asyncio

from telethon.tl.functions.messages import GetCustomEmojiDocumentsRequest

import telegram_common as tg


async def main(emoji_id):
    async with tg.make_client() as client:
        docs = await client(GetCustomEmojiDocumentsRequest(document_id=[int(emoji_id)]))
        if not docs:
            print("Эмодзи не найден.")
            return
        path = await client.download_media(docs[0], file=f"emoji_{emoji_id}")
        print(f"Сохранено: {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Скачать кастомный эмодзи по emoji-id")
    parser.add_argument("emoji_id", help="значение emoji-id из тега <tg-emoji>")
    args = parser.parse_args()

    tg.setup()
    asyncio.run(main(args.emoji_id))