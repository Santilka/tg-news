# text_processing.py
import json
import logging
import os
import re

from openai import OpenAI

log = logging.getLogger("telegram")

EMPTY_TEXT = "Новость без комментариев"
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

REWRITE_PROMPT = """Ты редактор новостей о киберспорте, конкретно о Dota 2. Из текста поста Telegram сделай заголовок и краткое описание для новостного сайта Dota 2-гильдии.

Контекст: упомянутые в тексте люди — это игроки, стримеры или каст-персоны Dota 2, а не музыканты/актёры/блогеры общего профиля. Упомянутые названия команд, организаций и ботов — это участники экосистемы Dota 2, а не сторонние сервисы или платформы, если из текста не следует обратное.

Правила:
- убери эмодзи, хэштеги, призывы подписаться, ссылки
- не придумывай факты, профессии, роли и детали, которых нет в тексте — если что-то не указано явно, не уточняй это
- заголовок — до 300 символов, суть поста
- описание — до 500 символов, 2-3 предложения
- ответь только JSON без пояснений: {{"title": "...", "description": "..."}}

Текст:
{text}"""


FOOTER_SPLIT = "<br><br>"
CUSTOM_EMOJI_RE = re.compile(r"<tg-emoji[^>]*>.*?</tg-emoji>", re.DOTALL)
EMPTY_TAG_RE = re.compile(r"<(strong|em|b|i|u)>\s*</\1>")
BLANK_LINE_RE = re.compile(r"\n\s*\n")


def clean_content(html):
    if FOOTER_SPLIT in html:
        html = html.rsplit(FOOTER_SPLIT, 1)[0]
    html = CUSTOM_EMOJI_RE.sub("", html)
    html = EMPTY_TAG_RE.sub("", html)
    return html.strip()


def strip_footer(text):
    parts = BLANK_LINE_RE.split(text.strip())
    return "\n\n".join(parts[:-1]) if len(parts) > 1 else text


def _groq_client():
    key = os.getenv("GROQ_API_KEY")
    return OpenAI(base_url="https://api.groq.com/openai/v1", api_key=key, timeout=20) if key else None


def rewrite_text(text):
    client = _groq_client()
    if not client or not (text or "").strip():
        return None
    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": REWRITE_PROMPT.format(text=text)}],
            temperature=0,
            max_tokens=800,
        )
        content = response.choices[0].message.content or ""
        start, end = content.find("{"), content.rfind("}")
        if start == -1 or end == -1:
            return None
        data = json.loads(content[start:end + 1])
        title = (data.get("title") or "").strip()[:300]
        description = (data.get("description") or "").strip()[:500]
        return {"title": title, "description": description or EMPTY_TEXT} if title else None
    except Exception:
        log.exception("Ошибка переписывания текста через Groq")
        return None