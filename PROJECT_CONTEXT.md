# PROJECT_CONTEXT: Telegram → новости forest-brothers.ru

Документ для ИИ-ассистента. Вместе с ним прикреплены файлы проекта. Прочитай всё до начала работы. Сначала кратко подтверди, что понял архитектуру, и только потом предлагай изменения.

---

## 0. Как работать с этим пользователем

- Язык: русский. Пользователя зовут Александр, он full-stack разработчик и сам ведёт проект.
- Стиль: прямо и кратко. Сначала короткое объяснение, потом реализация.
- Код: минимум зависимостей, чистые модульные функции, минимум комментариев (без больших docstring и блоков комментариев).
- Тестирование: инкрементально, маленькими шагами. После каждого шага давай команду проверки и жди результат.
- Уточняй неясное до того, как писать большой ответ. Не выдумывай содержимое файлов, которых не видел. Если нужен файл, попроси прислать конкретный файл или функцию.
- Локальная разработка: Windows, PowerShell, проект в `E:\site\`, виртуальное окружение `dota_env`. Серверы: Linux (bash). В командах всегда указывай, для какой ОС они.
- Секреты (API-хеш, пароли, приватные ключи, `.env`, содержимое `SECRET_KEY`) не проси присылать. Если нужно проверить `.env`, предлагай команды, которые выводят только имена переменных или `True/False`. Если пользователь всё же прислал секрет, напомни, что его лучше перевыпустить.

---

## 1. Проект в целом

**forest-brothers.ru**: веб-приложение для управления гильдией Dota 2.

- Стек: Django 5.2, PostgreSQL, Gunicorn, Nginx, Python.
- Пакет проекта Django: `dotaDja` (`dotaDja.settings`, `dotaDja.urls`, `dotaDja.wsgi`).
- Приложения в `INSTALLED_APPS`: `dotascore`, `news`, `widgets`, `calendar_app` и другие. К новостям относятся `news` (модели, админка, сервисы) и `dotascore` (главная страница, `home_view`, базовый шаблон `dotascore/index.html`, `dotascore/services/media_sync.py`).
- `settings.py` подгружает `.env` через `python-dotenv` (`load_dotenv()`). База читается из `PG_HOST`, `PG_DATABASE`, `PG_USER`, `PG_PASSWORD`. Порт в `DATABASES` жёстко задан как `5432`. `USE_TZ = True`, `TIME_ZONE = 'Europe/Moscow'`, `LANGUAGE_CODE = 'ru'`.
- В `settings.py` есть `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SESSION_PATH = BASE_DIR / "news" / "telegram" / "news_parser"` и `TELEGRAM_CHANNELS`, который читается через `os.environ["TELEGRAM_CHANNELS"]`. Если этой переменной нет в `.env`, сайт падает с `KeyError`. Для Telegram-скриптов на зарубежном VPS она не используется, но Django требует её наличия.

### Серверы и окружения

| Окружение | Что там |
|---|---|
| Локально (Windows) | `E:\site\`, venv `dota_env`, разработка; Telegram отсюда доступен; SSH-ключа для загрузки картинок нет, поэтому картинки локально не загружаются |
| Основной сервер (РФ) | Django + Gunicorn; `gunicorn.service` работает от пользователя `santila`; проект `/home/santila/site`, venv `/home/santila/site/venv`; **Telegram здесь ограничен** |
| Сервер PostgreSQL и статики (РФ) | база данных и Nginx для media; по IP в `MediaSync` судя по всему это один и тот же сервер (проверь, если важно) |
| Зарубежный VPS | доступен по SSH, Telegram работает; **здесь запускаются Telegram-скрипты** |

Деплой основного сайта идёт через GitHub. Секреты лежат в `.env` на каждой машине и в Git не попадают.

### Загрузка картинок

- `news/services/news_image_service.py` (`NewsImageService`): допустимые типы JPEG/PNG/WebP/GIF, лимит 5 МБ, ресайз до ширины 800 px, конвертация в WebP (качество 85), удаленные файлы имеют вид `news/<uuid>.webp`. `upload()` возвращает `{'url','filename','remote_path'}` или `{'error': ...}`. `NewsImageService.delete()` удаляет файл.
- `dotascore/services/media_sync.py` (`MediaSync`): загрузка по SFTP через `paramiko` на Nginx-сервер; пользователь `media_upload`, приватный **RSA**-ключ `~/.ssh/media_upload_key` (читается как `paramiko.RSAKey`, ключи Ed25519 не подойдут), каталог на сервере `/var/www/media/`, `AutoAddPolicy` для host key.
- Публичный URL картинки: `https://forest-brothers.ru/media/news/<uuid>.webp`.
- Версии на основном сервере: paramiko 4.0.0, pillow 11.3.0, psycopg2(-binary) 2.9.11, python-dotenv 1.1.1.

---

## 2. Задача

Автоматически превращать посты из публичных Telegram-каналов в новости сайта.

Потому что с основного сервера Telegram недоступен, парсер **вынесен в самостоятельные скрипты без Django**, которые работают на зарубежном VPS и пишут напрямую в ту же PostgreSQL. Сайт ничего об этом не знает: он просто показывает записи из таблиц.

```text
Telegram ──► зарубежный VPS ──► (SSH-туннель) ──► PostgreSQL ◄── Django (сайт, админка)
              telegram_import.py                      ▲
              telegram_listener.py                    │
                    │                                 │
                    └── SFTP ──► Nginx-сервер (media) ┘  (картинки WebP)
```

Правила:
- один обычный пост = одна `NewsItem`;
- альбом (общий `grouped_id`) = одна `NewsItem` + несколько `NewsImage`;
- правка поста обновляет существующую новость;
- удаление поста снимает новость с публикации (`is_published = false`), запись остаётся;
- публикация новых новостей зависит от `TelegramSettings.auto_publish` (по умолчанию выключено, новость создаётся снятой с публикации);
- дубли исключены уникальностью `(channel_id, message_id)` в `news_telegrammessage`.

---

## 3. Схема БД (создаётся миграциями Django, приложение `news`)

Миграции: `0001`–`0004` (`0004_newstag_telegramsettings_newsitem_source_type_and_more`). Скрипты пишут в таблицы обычным SQL, поэтому **любое изменение моделей `news` требует правки SQL в `telegram_common.py`**.

**`news_newsitem`**: `id`, `title` (300), `slug` (300, уникальный), `description` (text), `content` (text), `image` (varchar 500, пустая строка, если картинки нет), `news_type` (`'dota'` | `'guild'`), `source_type` (`'manual'` | `'telegram'`), `date` (timestamptz, может быть NULL), `source` (200), `external_link` (URL 500), `internal_link` (200), `is_published`, `created_at`, `updated_at`. Значения `created_at` и `updated_at` при прямом SQL заполняются вручную (`now()`).

**`news_newsimage`**: `id`, `news_id` (FK, CASCADE), `image` (varchar 500, URL), `"order"` (в SQL имя нужно брать в кавычки, это зарезервированное слово), `created_at`.

**`news_newstag`**: `id`, `name` (уникальное), `slug` (уникальный).

**`news_newsitem_tags`**: `newsitem_id`, `newstag_id`.

**`news_telegramsettings`**: `id`, `auto_publish` (по умолчанию `false`), `updated_at`. Ожидается одна строка (в Django `TelegramSettings.get_solo()`).

**`news_telegramchannel`**: `id`, `name`, `username` (без `@`), `telegram_id` (bigint, уникальный, может быть NULL, заполняется скриптами), `enabled`, `created_at`, `updated_at`.

**`news_telegramchannel_tags`**: `telegramchannel_id`, `newstag_id`. Теги канала присваиваются его новостям.

**`news_telegrammessage`**: `id`, `channel_id`, `message_id`, `grouped_id` (NULL для одиночных), `news_id` (FK, SET_NULL), `telegram_url`, `message_date`, `created_at`, `updated_at`; уникальность `(channel_id, message_id)`.

Каналы, теги и переключатель автопубликации редактируются через Django-админку. Скрипты только читают каналы и настройку.

### Что на стороне Django (не Telegram-скрипты)

- `news/models.py`: `NewsTag`, `NewsItem`, `NewsImage`, `TelegramSettings`, `TelegramChannel`, `TelegramMessage`. У `NewsItem.link` логика такая: для `guild` и `telegram` ведёт на внутреннюю страницу (`get_absolute_url`), для старых ручных `dota`-новостей на `external_link`. Сигналы `pre_delete` удаляют картинки с сервера через `NewsImageService.delete`.
- `news/admin.py`: админка всех моделей (у `TelegramMessage` только чтение).
- `news/views.py`, `news/services/news_renderer.py`, `news/templates/news/detail.html`: страница новости; в тексте заменяются `{{ "STEAM_ID"|player_name }}` на ник игрока; HTML из Telegram выводится через `|safe`. `render_news_content()` сначала санитайзит `content` через `bleach` (белый список: `p, br, strong, b, em, i, u, s, del, blockquote, code, pre, a, ul, ol, li`; для `a` — только `href` с `http`/`https`), потом подставляет никнейм — порядок важен, иначе `bleach` вырежет `<span style="color:red;">` для ненайденного игрока. На сервере нужно поставить `bleach` в venv.
- `dotascore/views.py` (`home_view`): выбирает опубликованные новости `dota` и `guild` для главной.
- Ранее написанный **Django-вариант импорта** (`news/services/telegram_importer.py`, команды `telegram_import` и `telegram_listener` в `news/management/commands/`) работал локально, но на основном сервере не применим из-за блокировки Telegram. Рабочим считается вариант со скриптами на зарубежном VPS. Судьбу Django-варианта (удалить или оставить для локальной разработки) пользователь ещё не решил.

---

## 4. Скрипты на зарубежном VPS

Каталог `~/tg-news/` (четыре файла + `.env` + файл сессии Telethon). Python 3.9+, зависимости: `telethon`, `cryptg`, `psycopg2-binary`, `python-dotenv`, `pillow`, `paramiko`, `openai` (для Groq).

### `telegram_common.py`: вся логика

- **Конфиг:** `.env` читается из каталога скриптов (не из cwd). `setup()` включает логирование и проверяет обязательные переменные. `make_client()` создаёт `TelegramClient`, сессия по умолчанию `~/tg-news/news_parser`.
- **База:** контекстный менеджер `db()` открывает новое соединение на каждую операцию (устойчиво к обрывам туннеля), коммит или откат делает сам. Функции: `db_channels`, `db_save_channel_tg_id`, `db_lookup(channel_id, message_ids)` (возвращает `(news_id, has_images)`), `db_album_ids`, `db_unpublish`, `save_publication(pub)`.
- **`save_publication(pub)`** (синхронная, запускается через `asyncio.to_thread`):
  1. ищет существующую новость по `news_telegrammessage`;
  2. если есть, обновляет `title/description/content/date/source/external_link` (`slug`, `is_published` и ручные правки остальных полей не трогает); если нет, создаёт новость (`slug` генерируется транслитерацией в латиницу (`TRANSLIT`/`slugify` в `telegram_common.py`, не Django `allow_unicode=True`) с числовым суффиксом при коллизии — так же должна работать генерация `slug` и на стороне Django в `NewsItem.save`; `is_published` берётся из `auto_publish`, при отсутствии строки настроек считается `false`);
  3. upsert записей `news_telegrammessage`;
  4. **добавляет** теги канала (`ON CONFLICT DO NOTHING`, существующие теги не удаляет);
  5. после коммита, если новость ещё без картинок, обрабатывает картинки (`process_image`) и записывает первую как `news_newsitem.image`, остальные и первую как `news_newsimage` с `"order"` 0, 1, 2…
- **Картинки:** `process_image(bytes)` открывает Pillow, приводит к RGB (прозрачность заливается фоном `(30, 30, 40)`), ресайзит до 800 px, сохраняет WebP q85 и вызывает `upload_bytes` (paramiko SFTP через `putfo`, без временных файлов). `MEDIA_URL_BASE + 'news/<uuid>.webp'` записывается в БД.
- **Текст:** заголовок и описание сначала пробуют получить через `tp.rewrite_text()` (Groq, после `tp.strip_footer()`), при `None` — фолбэк на `extract_title`/`extract_description` из `raw_text` (не из `.text`, где Telethon по умолчанию отдаёт markdown): заголовок это первая непустая строка (до 300 символов), описание это строки 2–4 (до 500 символов); если текста нет — «Новость без комментариев». `content` это `text_html` с заменой переносов строк на `<br>`, прогнанный через `tp.clean_content()` (подвал и `tg-emoji` вырезаны); ссылка на оригинал в `content` **не добавляется**, её выводит шаблон по `external_link`.
- **Telegram:** `group_messages` (склейка альбомов, пропуск служебных сообщений), `resolve_channel`, `album_messages`, `import_publication(client, channel, entity, messages, only_new=False)` (скачивает фото только если у новости ещё нет картинок), `import_channel(client, channel, limit, only_new=False)`.

### `text_processing.py`

LLM-обработка и чистка текста, отдельный модуль (импортируется как `tp` в `telegram_common.py`). `_groq_client()` — клиент `openai.OpenAI` на `base_url="https://api.groq.com/openai/v1"`, модель `GROQ_MODEL` (по умолчанию `openai/gpt-oss-120b`); если `GROQ_API_KEY` не задан — `None`. `rewrite_text(text)` — просит LLM выдать `{"title", "description"}` (промпт учитывает контекст Dota 2, запрещает выдумывать факты), при любой ошибке/пустом ответе возвращает `None`. `strip_footer(text)` — убирает последний абзац (по пустой строке) из plain text — рекламный подвал канала — перед отправкой в LLM. `clean_content(html)` — то же для HTML `content`: обрезает подвал по последнему `<br><br>`, вырезает `<tg-emoji>` целиком (вместе с юникод-эмодзи внутри), убирает опустевшие теги (`<strong></strong>` и т.п.).

### `telegram_import.py`

Ручной запуск: `python telegram_import.py --limit N` (по умолчанию 10). Для каждого включённого канала берёт последние N публикаций (альбом считается одной) и импортирует или обновляет. При первом запуске Telethon интерактивно запрашивает телефон, код и пароль двухфакторной защиты.

### `telegram_listener.py`

Постоянный процесс. Читает список включённых каналов **один раз при старте** (после добавления канала в админке сервис нужно перезапустить). Обработчики:
- `NewMessage` (одиночные посты; сообщения альбомов пропускаются);
- `Album`;
- `MessageEdited` (для альбома собирает все его сообщения по `grouped_id` из БД);
- `MessageDeleted` (снимает новость с публикации).

При старте с `--catchup N` (по умолчанию 5) догружает **только новые** публикации из последних N, чтобы не терять посты за время простоя (существующие новости при этом не перезаписываются). `--catchup 0` отключает догрузку.

### `.env` на зарубежном VPS (имена переменных)

Обязательные: `PG_HOST`, `PG_DATABASE`, `PG_USER`, `PG_PASSWORD`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `MEDIA_HOST`, `MEDIA_USER`.
Необязательные: `PG_PORT` (5432), `TELEGRAM_SESSION`, `MEDIA_KEY` (`~/.ssh/media_upload_key`), `MEDIA_PORT` (22), `MEDIA_REMOTE_PATH` (`/var/www/media/`), `MEDIA_URL_BASE` (`https://forest-brothers.ru/media/`), `GROQ_API_KEY` (без него `rewrite_text` не работает, фолбэк на regex-логику), `GROQ_MODEL` (`openai/gpt-oss-120b`).

---

## 5. Развёртывание на зарубежном VPS

1. **Доступ к БД напрямую по IP** (без SSH-туннеля): на сервере PostgreSQL разрешён доступ с конкретного IP зарубежного VPS (`pg_hba.conf` + `listen_addresses`/firewall на порт 5432 для этого IP). `PG_HOST` в `.env` на VPS — публичный IP сервера БД, `PG_PORT` — стандартный 5432.
2. **Ключ для картинок:** на зарубежном VPS создать RSA-ключ `ssh-keygen -t rsa -b 4096 -f ~/.ssh/media_upload_key`, публичную часть добавить пользователю `media_upload` на Nginx-сервере (и разрешить туда IP зарубежного VPS, если доступ по SSH ограничен). Приватные ключи между машинами не копировать.
3. **Сессия Telegram:** один раз выполнить `venv/bin/python telegram_import.py --limit 1` на этом VPS. Файл `news_parser.session` даёт доступ к аккаунту: не коммитить, не пересылать. Для `listener` аккаунт сессии должен быть **подписан** на каналы, для `import` подписка не нужна (публичные каналы).
4. **Listener под systemd** (`forest-brothers-telegram.service`):

   ```ini
   [Unit]
   Description=Forest Brothers Telegram Listener
   After=network-online.target
   Wants=network-online.target

   [Service]
   Type=simple
   User=<пользователь>
   WorkingDirectory=/home/<пользователь>/tg-news
   ExecStart=/home/<пользователь>/tg-news/venv/bin/python telegram_listener.py
   Restart=always
   RestartSec=10
   StandardOutput=journal
   StandardError=journal

   [Install]
   WantedBy=multi-user.target
   ```

   Команды: `sudo systemctl daemon-reload`, `sudo systemctl enable --now forest-brothers-telegram`, логи `journalctl -u forest-brothers-telegram -f`. Сначала listener запускается вручную, systemd подключается после успешной проверки.

Порядок проверки: (1) `python -c "import telegram_common as t; t.setup(); print(t.db_channels())"`; (2) `telegram_import.py --limit 1`; (3) `telegram_listener.py` вручную; (4) systemd.

---

## 6. Текущий статус

Сделано:
- модели, миграции, админка, страница новости, главная (Django-часть) работают;
- Django-вариант импорта проверен локально: авторизация, `telegram_import --limit 1` создал новость из канала `GabeNews_dota2`; картинки локально не загрузились из-за отсутствия SSH-ключа (ожидаемо);
- три автономных скрипта развёрнуты и проверены на зарубежном VPS: доступ к БД по IP работает, RSA-ключ для картинок создан и SFTP-загрузка работает, сессия Telegram авторизована, `telegram_import.py --limit 1` успешно создаёт новость с картинкой и идемпотентен при повторном запуске; **`telegram_listener.py` на VPS вручную ещё не запускали** — это следующий шаг;
- добавлен `text_processing.py`: `rewrite_text()` — LLM (Groq) переписывает `title`/`description` из текста поста, с фолбэком на `extract_title`/`extract_description` при сбое или отсутствии `GROQ_API_KEY`; `strip_footer()` — обрезает рекламный подвал канала из текста перед отправкой в LLM; `clean_content()` — обрезает тот же подвал и кастомные Telegram-эмодзи (`<tg-emoji>`) из HTML для `content`. Подключено в `import_publication` (`telegram_common.py`). Проверено локально на синтетических примерах постов;
- в `news/services/news_renderer.py` добавлена `bleach`-санитизация `content` (белый список тегов) перед выводом через `|safe` — на сервер ещё не задеплоено, нужно поставить `bleach` в `/home/santila/site/venv`.

## 7. Следующие шаги по обработке текста

- В `content` (HTML) ещё не чистятся хэштеги и явные текстовые призывы «подпишись на канал» (не ссылка, а именно текст) — примеры от пользователя пока не собраны, отложено.
- Идея на будущее: постить готовую (обработанную) новость в собственный Telegram-канал — отдельная задача, требует прав на публикацию у аккаунта `news_parser.session` (или отдельного бота) и решения по кастомным эмодзи (без Telegram Premium у аккаунта они не отправляются как кастомные, только юникод-фолбэк). Отложено, вернуться позже.

---

## 8. Какие файлы прикладывать к чату

Всегда: `PROJECT_CONTEXT.md` (этот файл), `telegram_common.py`, `text_processing.py`, `telegram_import.py`, `telegram_listener.py`.

По необходимости для доработок Django-стороны: `news/models.py`, `news/admin.py`, `news/views.py`, `news/services/news_image_service.py`, `news/services/news_renderer.py`, `news/templates/news/detail.html`, `dotascore/services/media_sync.py`, функцию `home_view` из `dotascore/views.py`. Из `settings.py` достаточно блока Telegram и `INSTALLED_APPS`, без секретов.

## 9. Стартовый запрос для нового чата

> Прочитай `PROJECT_CONTEXT.md` и приложенные файлы. Кратко подтверди, как устроена архитектура, и скажи, что из «Текущего статуса» осталось проверить. Дальше работаем инкрементально: я запускаю шаги, присылаю вывод, ты помогаешь с ошибками.
