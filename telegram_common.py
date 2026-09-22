import asyncio
import io
import logging
import os
import posixpath
import re
import unicodedata
import uuid
from contextlib import contextmanager
from html import escape
from pathlib import Path
from telethon.extensions import html as tl_html

import paramiko
import psycopg2
from dotenv import load_dotenv
from PIL import Image
from telethon import TelegramClient

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

log = logging.getLogger("telegram")

EMPTY_TEXT = "Новость без комментариев"
MAX_WIDTH = 800
WEBP_QUALITY = 85
REMOTE_DIR = "news/"
MEDIA_URL_BASE = os.getenv("MEDIA_URL_BASE", "https://forest-brothers.ru/media/")

REQUIRED_ENV = (
    "PG_HOST", "PG_DATABASE", "PG_USER", "PG_PASSWORD",
    "TELEGRAM_API_ID", "TELEGRAM_API_HASH",
    "MEDIA_HOST", "MEDIA_USER",
)

UPSERT_MESSAGE = (
    "INSERT INTO news_telegrammessage "
    "(channel_id, message_id, grouped_id, news_id, telegram_url, message_date, created_at, updated_at) "
    "VALUES (%s,%s,%s,%s,%s,%s,now(),now()) "
    "ON CONFLICT (channel_id, message_id) DO UPDATE SET "
    "grouped_id=EXCLUDED.grouped_id, news_id=EXCLUDED.news_id, "
    "telegram_url=EXCLUDED.telegram_url, message_date=EXCLUDED.message_date, updated_at=now()"
)


def setup():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("telethon").setLevel(logging.WARNING)
    missing = [name for name in REQUIRED_ENV if not os.getenv(name)]
    if missing:
        raise SystemExit("Не заданы переменные .env: " + ", ".join(missing))


def make_client():
    session = os.path.expanduser(os.getenv("TELEGRAM_SESSION") or str(BASE_DIR / "news_parser"))
    return TelegramClient(session, int(os.environ["TELEGRAM_API_ID"]), os.environ["TELEGRAM_API_HASH"])


# ---------- база ----------

@contextmanager
def db():
    conn = psycopg2.connect(
        host=os.environ["PG_HOST"],
        port=os.getenv("PG_PORT", "5432"),
        dbname=os.environ["PG_DATABASE"],
        user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"],
        connect_timeout=10,
    )
    try:
        with conn:
            with conn.cursor() as cur:
                yield cur
    finally:
        conn.close()


def db_channels():
    with db() as cur:
        cur.execute("SELECT id, name, username FROM news_telegramchannel WHERE enabled ORDER BY name")
        return [{"id": r[0], "name": r[1], "username": r[2] or ""} for r in cur.fetchall()]


def db_save_channel_tg_id(channel_id, tg_id):
    with db() as cur:
        cur.execute(
            "UPDATE news_telegramchannel SET telegram_id=%s, updated_at=now() "
            "WHERE id=%s AND telegram_id IS DISTINCT FROM %s",
            (tg_id, channel_id, tg_id),
        )


def _find_news(cur, channel_id, message_ids):
    cur.execute(
        "SELECT news_id FROM news_telegrammessage "
        "WHERE channel_id=%s AND message_id = ANY(%s) AND news_id IS NOT NULL "
        "ORDER BY message_id LIMIT 1",
        (channel_id, list(message_ids)),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _has_images(cur, news_id):
    cur.execute("SELECT EXISTS(SELECT 1 FROM news_newsimage WHERE news_id=%s)", (news_id,))
    return cur.fetchone()[0]


def db_lookup(channel_id, message_ids):
    with db() as cur:
        news_id = _find_news(cur, channel_id, message_ids)
        return news_id, bool(news_id and _has_images(cur, news_id))


def db_album_ids(channel_id, grouped_id):
    with db() as cur:
        cur.execute(
            "SELECT message_id FROM news_telegrammessage "
            "WHERE channel_id=%s AND grouped_id=%s ORDER BY message_id",
            (channel_id, grouped_id),
        )
        return [r[0] for r in cur.fetchall()]


def db_unpublish(channel_id, message_ids):
    with db() as cur:
        cur.execute(
            "UPDATE news_newsitem SET is_published=false, updated_at=now() "
            "WHERE id IN (SELECT news_id FROM news_telegrammessage "
            "WHERE channel_id=%s AND message_id = ANY(%s) AND news_id IS NOT NULL)",
            (channel_id, list(message_ids)),
        )


TRANSLIT = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p",
     "r", "s", "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"],
))
SLUG_LIMIT = 60


def limit_slug(slug, limit=SLUG_LIMIT):
    if len(slug) <= limit:
        return slug
    cut = slug[:limit]
    if slug[limit] != "-" and "-" in cut:
        cut = cut.rsplit("-", 1)[0]
    return cut.strip("-")


def slugify(value):
    value = unicodedata.normalize("NFKC", value).lower()
    value = "".join(TRANSLIT.get(c, c) for c in value)
    value = re.sub(r"[^a-z0-9\s-]", "", value)
    return limit_slug(re.sub(r"[-\s]+", "-", value).strip("-"))


def _unique_slug(cur, title):
    base = (slugify(title) or f"news-{uuid.uuid4().hex[:10]}")[:300]
    slug, counter = base, 2
    while True:
        cur.execute("SELECT 1 FROM news_newsitem WHERE slug=%s", (slug,))
        if not cur.fetchone():
            return slug
        suffix = f"-{counter}"
        slug = f"{base[:300 - len(suffix)]}{suffix}"
        counter += 1


def save_publication(pub):
    ids = [m[0] for m in pub["messages"]]

    with db() as cur:
        news_id = _find_news(cur, pub["channel_id"], ids)

        if news_id:
            cur.execute(
                "UPDATE news_newsitem SET title=%s, description=%s, content=%s, date=%s, source=%s, "
                "external_link=%s, source_type='telegram', news_type='dota', updated_at=now() WHERE id=%s",
                (pub["title"], pub["description"], pub["content"], pub["date"],
                 pub["source"], pub["url"], news_id),
            )
        else:
            cur.execute("SELECT auto_publish FROM news_telegramsettings ORDER BY id LIMIT 1")
            row = cur.fetchone()
            cur.execute(
                "INSERT INTO news_newsitem (title, slug, description, content, image, news_type, "
                "source_type, date, source, external_link, internal_link, is_published, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,'','dota','telegram',%s,%s,%s,'',%s,now(),now()) RETURNING id",
                (pub["title"], _unique_slug(cur, pub["title"]), pub["description"], pub["content"],
                 pub["date"], pub["source"], pub["url"], bool(row and row[0])),
            )
            news_id = cur.fetchone()[0]

        for message_id, grouped_id, url, date in pub["messages"]:
            cur.execute(UPSERT_MESSAGE, (pub["channel_id"], message_id, grouped_id, news_id, url, date))

        cur.execute(
            "INSERT INTO news_newsitem_tags (newsitem_id, newstag_id) "
            "SELECT %s, newstag_id FROM news_telegramchannel_tags WHERE telegramchannel_id=%s "
            "ON CONFLICT DO NOTHING",
            (news_id, pub["channel_id"]),
        )
        has_images = _has_images(cur, news_id)

    if pub["images"] and not has_images:
        urls = [url for url in map(process_image, pub["images"]) if url]
        if urls:
            with db() as cur:
                cur.execute(
                    "UPDATE news_newsitem SET image=%s, updated_at=now() WHERE id=%s AND image=''",
                    (urls[0], news_id),
                )
                for order, url in enumerate(urls):
                    cur.execute(
                        'INSERT INTO news_newsimage (news_id, image, "order", created_at) '
                        "VALUES (%s,%s,%s,now())",
                        (news_id, url, order),
                    )

    return news_id


# ---------- картинки ----------

def process_image(data):
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        log.warning("Файл не является изображением")
        return None

    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        background = Image.new("RGB", img.size, (30, 30, 40))
        background.paste(img, mask=img.split()[-1])
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")

    if img.width > MAX_WIDTH:
        height = int(img.height * MAX_WIDTH / img.width)
        img = img.resize((MAX_WIDTH, height), Image.Resampling.LANCZOS)

    buffer = io.BytesIO()
    img.save(buffer, "WEBP", quality=WEBP_QUALITY)

    remote_subpath = f"{REMOTE_DIR}{uuid.uuid4().hex}.webp"
    if not upload_bytes(buffer.getvalue(), remote_subpath):
        return None
    return MEDIA_URL_BASE + remote_subpath


def _mkdir_p(sftp, path):
    missing = []
    while path not in ("", "/"):
        try:
            sftp.stat(path)
            break
        except FileNotFoundError:
            missing.append(path)
            path = posixpath.dirname(path)
    for directory in reversed(missing):
        sftp.mkdir(directory)
        sftp.chmod(directory, 0o755)


def upload_bytes(data, remote_subpath):
    try:
        key = paramiko.RSAKey.from_private_key_file(
            os.path.expanduser(os.getenv("MEDIA_KEY", "~/.ssh/media_upload_key"))
        )
        with paramiko.SSHClient() as ssh:
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                hostname=os.environ["MEDIA_HOST"],
                port=int(os.getenv("MEDIA_PORT", "22")),
                username=os.environ["MEDIA_USER"],
                pkey=key,
                timeout=10,
            )
            sftp = ssh.open_sftp()
            remote_file = posixpath.join(os.getenv("MEDIA_REMOTE_PATH", "/var/www/media/"), remote_subpath)
            _mkdir_p(sftp, posixpath.dirname(remote_file))
            sftp.putfo(io.BytesIO(data), remote_file)
            sftp.chmod(remote_file, 0o644)
            sftp.close()
        return True
    except Exception:
        log.exception("Ошибка загрузки файла на Nginx VPS")
        return False


# ---------- текст ----------

def _lines(text):
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def extract_title(text):
    lines = _lines(text)
    return lines[0][:300] if lines else EMPTY_TEXT


def extract_description(text):
    lines = _lines(text)
    if len(lines) <= 1:
        return EMPTY_TEXT
    description = " ".join(lines[1:4])
    return description if len(description) <= 500 else description[:499].rstrip() + "…"


def build_html(message):
    text = message.raw_text or ""
    body = tl_html.unparse(text, message.entities) if message.entities else escape(text)
    return body.replace("\n", "<br>")


def entity_name(entity):
    return getattr(entity, "title", None) or getattr(entity, "username", None) or "Telegram"


def message_url(entity, message_id):
    username = getattr(entity, "username", None)
    return f"https://t.me/{username}/{message_id}" if username else ""


# ---------- Telegram ----------

def group_messages(messages):
    groups, singles = {}, []
    for message in messages:
        if not message or getattr(message, "action", None):
            continue
        if message.grouped_id:
            groups.setdefault(message.grouped_id, []).append(message)
        else:
            singles.append([message])

    publications = list(groups.values()) + singles
    publications.sort(key=lambda group: max(m.date for m in group), reverse=True)
    return publications


async def resolve_channel(client, channel):
    entity = await client.get_entity(channel["username"].lstrip("@"))
    await asyncio.to_thread(db_save_channel_tg_id, channel["id"], entity.id)
    return entity


async def album_messages(client, channel, entity, grouped_id):
    ids = await asyncio.to_thread(db_album_ids, channel["id"], grouped_id)
    if not ids:
        return []
    return [m for m in await client.get_messages(entity, ids=ids) if m]


async def import_publication(client, channel, entity, messages, only_new=False):
    messages = sorted(messages, key=lambda m: m.id)

    news_id, has_images = await asyncio.to_thread(
        db_lookup, channel["id"], [m.id for m in messages]
    )
    if news_id and only_new:
        return None

    canonical = next((m for m in messages if (m.raw_text or "").strip()), messages[0])
    text = canonical.raw_text or ""

    images = []
    if not has_images:
        for message in messages:
            if not message.photo:
                continue
            try:
                data = await message.download_media(file=bytes)
            except Exception:
                log.exception("Не удалось скачать фото message=%s", message.id)
                continue
            if data:
                images.append(data)

    pub = {
        "channel_id": channel["id"],
        "source": entity_name(entity),
        "title": extract_title(text),
        "description": extract_description(text),
        "content": build_html(canonical),
        "date": canonical.date,
        "url": message_url(entity, canonical.id),
        "messages": [
            (m.id, m.grouped_id, message_url(entity, m.id), m.date) for m in messages
        ],
        "images": images,
    }
    return await asyncio.to_thread(save_publication, pub)


async def import_channel(client, channel, limit, only_new=False):
    entity = await resolve_channel(client, channel)
    messages = [m async for m in client.iter_messages(entity, limit=max(limit * 10, 100))]

    done = 0
    for publication in group_messages(messages)[:limit]:
        try:
            if await import_publication(client, channel, entity, publication, only_new):
                done += 1
        except Exception:
            log.exception("Не удалось импортировать публикацию (%s)", channel["name"])
    return done
