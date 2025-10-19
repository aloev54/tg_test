#!/usr/bin/env python3
"""
site_to_telegram.py
Fetches a website (or RSS), extracts new items, and auto-posts them to a Telegram channel.
- Uses a simple CSS selector to find article links on an HTML page OR consumes an RSS/Atom feed.
- Remembers what has already been posted in a local JSON file.
- Safe to run periodically via cron, systemd timer, or GitHub Actions.

Quick start (HTML page with CSS selector):
    export TELEGRAM_BOT_TOKEN="123456:ABC..."
    export TELEGRAM_CHAT_ID="@your_channel_or_chat_id"
    python site_to_telegram.py \
        --mode html \
        --url "https://example.com/blog" \
        --item-selector "article h2 a" \
        --base-url "https://example.com" \
        --post-prefix "📰 Новая публикация:" \
        --limit 5

Quick start (RSS/Atom feed):
    export TELEGRAM_BOT_TOKEN="123456:ABC..."
    export TELEGRAM_CHAT_ID="@your_channel_or_chat_id"
    python site_to_telegram.py \
        --mode rss \
        --url "https://example.com/feed.xml" \
        --post-prefix "📰 Новая публикация:" \
        --limit 5
"""
import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple
from urllib.parse import urljoin

import requests

# feedparser is optional; we gate-import it when needed.
try:
    import feedparser  # type: ignore
except Exception:
    feedparser = None

try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception as e:
    print("BeautifulSoup (bs4) is required for HTML parsing. Add it to requirements.txt and install.", file=sys.stderr)
    raise

STATE_FILE = "seen.json"
DEFAULT_UA = "Mozilla/5.0 (compatible; site2tg/1.0; +https://example.com)"

TELEGRAM_API_BASE = "https://api.telegram.org"

@dataclass
class Item:
    title: str
    url: str
    summary: Optional[str] = None


def load_seen(path: str) -> set:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return set(data)
        except Exception:
            return set()
    return set()


def save_seen(path: str, seen: set) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(list(seen)), f, ensure_ascii=False, indent=2)


def slugify(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9\-._:/?#\[\]@!$&'()*+,;=%]", "", s)
    return s


def hash_id(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def fetch_html(url: str, headers: Optional[dict] = None, timeout: int = 20) -> str:
    h = {"User-Agent": DEFAULT_UA}
    if headers:
        h.update(headers)
    r = requests.get(url, headers=h, timeout=timeout)
    r.raise_for_status()
    return r.text


def extract_items_html(html_text: str, base_url: Optional[str], css_selector: str, limit: int) -> List[Item]:
    soup = BeautifulSoup(html_text, "html.parser")
    nodes = soup.select(css_selector)[:limit or None]
    items: List[Item] = []
    for node in nodes:
        # Try to get link + title
        if node.name != "a":
            link = node.find("a")
        else:
            link = node
        if not link or not link.get("href"):
            continue
        url = link.get("href")
        if base_url:
            url = urljoin(base_url, url)
        title = link.get_text(strip=True) or url
        # Try to get a short summary from nearby element
        summary_node = node.find_next("p")
        summary = summary_node.get_text(strip=True) if summary_node else None
        items.append(Item(title=title, url=url, summary=summary))
    return items


def extract_items_rss(feed_url: str, limit: int) -> List[Item]:
    if feedparser is None:
        raise RuntimeError("feedparser is required for RSS/Atom mode. Add it to requirements.txt and install.")
    feed = feedparser.parse(feed_url)
    items: List[Item] = []
    for entry in feed.entries[:limit or None]:
        title = entry.get("title") or "(без названия)"
        link = entry.get("link") or ""
        summary = None
        if "summary" in entry:
            summary = re.sub("<[^<]+?>", "", entry.summary or "").strip()
        items.append(Item(title=title, url=link, summary=summary))
    return items


def build_message(item: Item, prefix: str = "", suffix: str = "", max_len: int = 3800) -> str:
    parts = []
    if prefix:
        parts.append(prefix.strip())
    # Escape HTML for Telegram parse_mode=HTML
    safe_title = html.escape(item.title)
    safe_url = html.escape(item.url)
    parts.append(f"<b>{safe_title}</b>\n{safe_url}")
    if item.summary:
        summary = item.summary.strip()
        # Trim summary
        if len(summary) > 1000:
            summary = summary[:1000].rstrip() + "…"
        parts.append(html.escape(summary))
    if suffix:
        parts.append(suffix.strip())
    text = "\n\n".join(parts).strip()
    if len(text) > max_len:
        text = text[: max_len - 1] + "…"
    return text


def telegram_send_message(token: str, chat_id: str, text: str, disable_preview: bool = False, retries: int = 3) -> Tuple[bool, str]:
    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": disable_preview,
    }
    last_err = ""
    for i in range(retries):
        try:
            r = requests.post(url, data=payload, timeout=20)
            if r.status_code == 200:
                return True, ""
            last_err = f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as e:
            last_err = str(e)
        time.sleep(2 * (i + 1))  # simple backoff
    return False, last_err


def main():
    parser = argparse.ArgumentParser(description="Scrape a site or RSS and post new items to Telegram.")
    parser.add_argument("--mode", choices=["html", "rss"], required=True, help="Parse HTML with CSS selector or use RSS/Atom feed.")
    parser.add_argument("--url", required=True, help="Source URL (page with articles for html mode or feed URL for rss mode).")
    parser.add_argument("--item-selector", help="CSS selector to find items (html mode). Example: 'article h2 a'")
    parser.add_argument("--base-url", help="Base URL to resolve relative links (html mode).")
    parser.add_argument("--limit", type=int, default=10, help="Max number of items to consider per run.")
    parser.add_argument("--state", default=STATE_FILE, help="Path to JSON file to track already-posted items.")
    parser.add_argument("--post-prefix", default="📰 Новая публикация:", help="Text to prepend to each post.")
    parser.add_argument("--post-suffix", default="", help="Text to append to each post.")
    parser.add_argument("--disable-preview", action="store_true", help="Disable link previews in Telegram.")
    parser.add_argument("--dry-run", action="store_true", help="Do not post to Telegram; just print.")
    args = parser.parse_args()

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("Please set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment variables.", file=sys.stderr)
        sys.exit(2)

    seen = load_seen(args.state)

    if args.mode == "html":
        if not args.item_selector:
            print("--item-selector is required in html mode (e.g., 'article h2 a')", file=sys.stderr)
            sys.exit(2)
        html_text = fetch_html(args.url)
        items = extract_items_html(html_text, args.base_url, args.item_selector, args.limit)
    else:
        items = extract_items_rss(args.url, args.limit)

    # Post newest first: reverse chronological by URL hash (approx), or keep given order
    # Many feeds/pages are already newest-first; we keep the original order but skip seen.
    posted_count = 0
    for item in items:
        uid = hash_id(item.url or item.title)
        if uid in seen:
            continue
        message = build_message(item, prefix=args.post_prefix, suffix=args.post_suffix)
        if args.dry_run:
            print("DRY RUN —— would post:\n", message, "\n", "-" * 40)
        else:
            ok, err = telegram_send_message(token, chat_id, message, disable_preview=args.disable_preview)
            if not ok:
                print(f"Failed to post '{item.title}': {err}", file=sys.stderr)
                continue
        seen.add(uid)
        posted_count += 1
        time.sleep(1.5)  # be gentle

    save_seen(args.state, seen)
    print(f"Done. Posted {posted_count} new item(s).")


if __name__ == "__main__":
    main()
