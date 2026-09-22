import argparse
import asyncio

from telethon import events, utils

import telegram_common as tg

log = tg.log


async def main(catchup):
    channels = await asyncio.to_thread(tg.db_channels)

    async with tg.make_client() as client:
        targets = {}
        for channel in channels:
            try:
                entity = await tg.resolve_channel(client, channel)
            except Exception:
                log.exception("Не удалось подключить канал %s", channel["name"])
                continue
            targets[entity.id] = (channel, entity)
            log.info("Слушаю: %s", channel["name"])

        if not targets:
            log.error("Нет доступных каналов.")
            return

        chats = [entity for _, entity in targets.values()]
        lock = asyncio.Lock()

        def target_of(event):
            if not event.chat_id:
                return None
            return targets.get(utils.resolve_id(event.chat_id)[0])

        async def process(event, messages):
            target = target_of(event)
            if not target or not messages:
                return
            channel, entity = target
            try:
                async with lock:
                    await tg.import_publication(client, channel, entity, messages)
                log.info("Импортирован пост %s (%s)", messages[0].id, channel["name"])
            except Exception:
                log.exception("Ошибка импорта поста %s (%s)", messages[0].id, channel["name"])

        @client.on(events.NewMessage(chats=chats))
        async def on_new(event):
            if not event.message.grouped_id:
                await process(event, [event.message])

        @client.on(events.Album(chats=chats))
        async def on_album(event):
            await process(event, list(event.messages))

        @client.on(events.MessageEdited(chats=chats))
        async def on_edit(event):
            message = event.message
            if message.grouped_id:
                target = target_of(event)
                if not target:
                    return
                messages = await tg.album_messages(client, target[0], target[1], message.grouped_id)
            else:
                messages = [message]
            await process(event, messages)

        @client.on(events.MessageDeleted(chats=chats))
        async def on_delete(event):
            target = target_of(event)
            if not target:
                return
            try:
                await asyncio.to_thread(tg.db_unpublish, target[0]["id"], event.deleted_ids)
                log.info("Сняты с публикации посты %s (%s)", event.deleted_ids, target[0]["name"])
            except Exception:
                log.exception("Ошибка снятия с публикации %s", event.deleted_ids)

        if catchup:
            for channel, _ in targets.values():
                try:
                    async with lock:
                        done = await tg.import_channel(client, channel, catchup, only_new=True)
                    log.info("Догрузка %s: новых публикаций %s", channel["name"], done)
                except Exception:
                    log.exception("Ошибка догрузки %s", channel["name"])

        log.info("Listener запущен.")
        await client.run_until_disconnected()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Фоновый Telegram listener")
    parser.add_argument(
        "--catchup", type=int, default=5,
        help="при старте догрузить пропущенные публикации из последних N (0 — выключить)",
    )
    args = parser.parse_args()

    tg.setup()
    try:
        asyncio.run(main(args.catchup))
    except KeyboardInterrupt:
        log.info("Listener остановлен.")