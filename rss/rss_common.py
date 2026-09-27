# tg-news/rss/rss_common.py

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
import re
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from urllib.parse import urljoin

import feedparser
import requests
import trafilatura
from bs4 import BeautifulSoup

from telegram_common import db, process_image

log = logging.getLogger("rss")

USER_AGENT = "forest-brothers.ru news-bot/1.0 (+https://forest-brothers.ru)"
REQUEST_DELAY = 1.5
TIMEOUT = 10


def db_sources():
    with db() as cur:
        cur.execute("SELECT id, name, feed_url FROM news_rsssource WHERE enabled ORDER BY name")
        return [{"id": r[0], "name": r[1], "feed_url": r[2]} for r in cur.fetchall()]


def db_exists(external_link):
    with db() as cur:
        cur.execute("SELECT 1 FROM news_newsitem WHERE external_link=%s", (external_link,))
        return cur.fetchone() is not None


TRANSLIT = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p",
     "r", "s", "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"],
))
SLUG_LIMIT = 60


def _slugify(value):
    value = unicodedata.normalize("NFKC", value).lower()
    value = "".join(TRANSLIT.get(c, c) for c in value)
    value = re.sub(r"[^a-z0-9\s-]", "", value)
    slug = re.sub(r"[-\s]+", "-", value).strip("-")
    if len(slug) <= SLUG_LIMIT:
        return slug
    cut = slug[:SLUG_LIMIT]
    if slug[SLUG_LIMIT] != "-" and "-" in cut:
        cut = cut.rsplit("-", 1)[0]
    return cut.strip("-")


def _unique_slug(cur, title):
    base = (_slugify(title) or f"news-{uuid.uuid4().hex[:10]}")[:300]
    slug, counter = base, 2
    while True:
        cur.execute("SELECT 1 FROM news_newsitem WHERE slug=%s", (slug,))
        if not cur.fetchone():
            return slug
        suffix = f"-{counter}"
        slug = f"{base[:300 - len(suffix)]}{suffix}"
        counter += 1


def _download_image(url):
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        resp.raise_for_status()
        return process_image(resp.content)
    except Exception:
        log.exception("Не удалось скачать/обработать картинку %s", url)
        return None


def _rehost_content_images(html, base_url):
    if not html:
        return html

    soup = BeautifulSoup(html, "html.parser")
    for img in soup.find_all("img"):
        src = img.get("src")
        if not src:
            img.decompose()
            continue

        local_url = _download_image(urljoin(base_url, src))
        time.sleep(REQUEST_DELAY)

        if local_url:
            img["src"] = local_url
        else:
            img.decompose()

    return str(soup)


def fetch_article(url):
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    resp.raise_for_status()
    html = resp.text

    soup = BeautifulSoup(html, "html.parser")
    og_image = soup.find("meta", attrs={"property": "og:image"})
    image_url = og_image["content"] if og_image and og_image.get("content") else None

    content_html_raw = trafilatura.extract(
        html,
        output_format="html",
        include_images=True,
        include_links=False,
        include_formatting=True,
    ) or ""
    content_html = _rehost_content_images(content_html_raw, url)
    content_text = trafilatura.extract(html) or ""

    return {"content_html": content_html, "content_text": content_text, "image_url": image_url}


def save_item(link, title, pub_date, source_name, article):
    description = article["content_text"][:497] + "…" if len(article["content_text"]) > 500 else article["content_text"]
    image_local = _download_image(article["image_url"]) if article["image_url"] else None

    with db() as cur:
        slug = _unique_slug(cur, title)
        cur.execute(
            "INSERT INTO news_newsitem (title, slug, description, content, image, news_type, "
            "source_type, date, source, external_link, internal_link, is_published, created_at, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,'dota','rss',%s,%s,%s,'',true,now(),now()) RETURNING id",
            (title[:300], slug, description, article["content_html"], image_local or "",
             pub_date, source_name, link),
        )
        news_id = cur.fetchone()[0]

        if image_local:
            cur.execute(
                'INSERT INTO news_newsimage (news_id, image, "order", created_at) VALUES (%s,%s,0,now())',
                (news_id, image_local),
            )
    return news_id


def import_feed(source, limit):
    feed = feedparser.parse(source["feed_url"])
    done = 0
    for entry in feed.entries[:limit]:
        link = entry.link.split("?")[0]
        if db_exists(link):
            continue
        try:
            article = fetch_article(link)
            pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc) if entry.get("published_parsed") else None
            save_item(link, entry.title, pub_date, source["name"], article)
        except Exception:
            log.exception("Не удалось обработать %s", link)
            continue
        done += 1
        time.sleep(REQUEST_DELAY)
    return done