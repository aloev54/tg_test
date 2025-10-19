# Site → Telegram автопостер

Этот мини-проект берёт материалы с указанного сайта (HTML-страница или RSS/Atom) и автоматически публикует новые записи в ваш Telegram-канал/чат.

## Что умеет
- HTML-режим: парсит страницу по CSS‑селектору (например, `article h2 a`) и достаёт заголовки/ссылки (и короткое описание).
- RSS‑режим: берёт новые элементы из RSS/Atom ленты.
- Запоминает, что уже публиковалось (`seen.json`), чтобы не дублировать.
- Форматирует пост: префикс → **жирный заголовок** → ссылка → краткое описание → суффикс.
- Безопасен к многократному запуску (подходит для cron/Systemd/GitHub Actions).

## Установка
```bash
cd site2tg
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Настройка Telegram
1) Создайте бота у @BotFather и получите `TELEGRAM_BOT_TOKEN`.
2) Добавьте бота админом в канал (или в чат).
3) Узнайте chat id: можно указать `@username` канала или числовой ID (для каналов часто начинается с `-100...`).

## Запуск (HTML)
```bash
export TELEGRAM_BOT_TOKEN="123456:ABC..."
export TELEGRAM_CHAT_ID="@your_channel_or_chat_id"

python site_to_telegram.py \
  --mode html \
  --url "https://example.com/blog" \
  --item-selector "article h2 a" \
  --base-url "https://example.com" \
  --post-prefix "📰 Новая публикация:" \
  --limit 5
```

## Запуск (RSS)
```bash
export TELEGRAM_BOT_TOKEN="123456:ABC..."
export TELEGRAM_CHAT_ID="@your_channel_or_chat_id"

python site_to_telegram.py \
  --mode rss \
  --url "https://example.com/feed.xml" \
  --post-prefix "📰 Новая публикация:" \
  --limit 5
```

Опции:
- `--post-suffix` — текст в конец поста (например, «🏎️ *РулЁжка* (https://t.me/drive_hedgehog)»).
- `--disable-preview` — отключить предпросмотр ссылок в Telegram.
- `--dry-run` — не постить, а только показать, что бы отправилось.
- `--state seen.json` — путь к файлу состояния (по умолчанию `seen.json`).

## Автозапуск по расписанию

### cron (Linux/macOS)
```bash
crontab -e
# каждые 15 минут
*/15 * * * * cd /path/to/site2tg && . .venv/bin/activate && TELEGRAM_BOT_TOKEN=xxx TELEGRAM_CHAT_ID=@your_channel python site_to_telegram.py --mode rss --url https://example.com/feed.xml --limit 5 >> cron.log 2>&1
```

### systemd timer (Linux)
Создайте `~/.config/systemd/user/site2tg.service`:
```
[Unit]
Description=Site to Telegram Poster

[Service]
WorkingDirectory=/path/to/site2tg
Environment=TELEGRAM_BOT_TOKEN=xxx
Environment=TELEGRAM_CHAT_ID=@your_channel
ExecStart=/path/to/site2tg/.venv/bin/python /path/to/site2tg/site_to_telegram.py --mode rss --url https://example.com/feed.xml --limit 5
```

И `~/.config/systemd/user/site2tg.timer`:
```
[Unit]
Description=Run site2tg every 15 minutes

[Timer]
OnBootSec=30s
OnUnitActiveSec=15m
Unit=site2tg.service

[Install]
WantedBy=timers.target
```

Активируем:
```bash
systemctl --user daemon-reload
systemctl --user enable --now site2tg.timer
```

### GitHub Actions (хостинг бесплатно, но с лимитами)
В репозитории создайте workflow `.github/workflows/site2tg.yml`:
```yaml
name: site2tg
on:
  schedule:
    - cron: "*/15 * * * *"
  workflow_dispatch: {}

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -r requirements.txt
      - env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: |
          python site_to_telegram.py --mode rss --url "https://example.com/feed.xml" --limit 5
```

## Настройка парсинга HTML
- Подберите CSS‑селектор, который указывает на ссылку статьи. Примеры:
  - `article h2 a`
  - `.post-title a`
  - `li.news-item > a`
- Если ссылки на странице относительные — укажите `--base-url` сайта.

## Советы
- Сначала запустите с `--dry-run`, проверьте формат поста, затем уберите флаг.
- Если на сайте есть RSS — лучше использовать `--mode rss`: стабильнее и быстрее.
- Для разных сайтов можно сделать несколько cron‑заданий с разными аргументами и разными `--state` файлами.
