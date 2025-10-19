#!/usr/bin/env python3
"""
site_to_telegram.py (updated)
- HTML mode: can optionally fetch each article page to resolve proper title/summary (og:title, og:description).
- RSS mode supported as before.
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

try:
    import feedparser  # type: ignore
except Exception:
    feedparser = None

try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:
    print("BeautifulSoup (bs4) is required for HTML parsing. Add it to requirements.txt and install.", file=sys.stderr)
    raise

STATE_FILE = "seen.json"
DEFAULT_UA = "Mozilla/5.0 (compatible; site2tg/1.1)"

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
        link = node if node.name == "a" else node.find("a")
        if not link or not link.get("href"):
            continue
        url = urljoin(base_url, link.get("href")) if base_url else link.get("href")
        title = link.get_text(strip=True) or url
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
    safe_title = html.escape(item.title)
    safe_url = html.escape(item.url)
    parts.append(f"<b>{safe_title}</b>\n{safe_url}")
    if item.summary:
        summary = item.summary.strip()
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
        time.sleep(2 * (i + 1))
    return False, last_err


def resolve_from_page(url: str) -> Tuple[Optional[str], Optional[str]]:
    try:
        html_text = fetch_html(url)
        soup = BeautifulSoup(html_text, "html.parser")
        title_tag = soup.find("meta", property="og:title")
        title = title_tag.get("content").strip() if title_tag and title_tag.get("content") else None
        if not title and soup.title and soup.title.string:
            title = soup.title.string.strip()
        desc_tag = soup.find("meta", property="og:description")
        summary = desc_tag.get("content").strip() if desc_tag and desc_tag.get("content") else None
        if not summary:
            p = soup.find("p")
            if p:
                summary = p.get_text(" ", strip=True)
        return title, summary
    except Exception:
        return None, None


def main():
    parser = argparse.ArgumentParser(description="Scrape a site or RSS and post new items to Telegram.")
    parser.add_argument("--mode", choices=["html", "rss"], required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--item-selector", help="CSS selector (html mode). Example: 'article h2 a'")
    parser.add_argument("--base-url", help="Base URL for relative links (html mode).")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--state", default=STATE_FILE)
    parser.add_argument("--post-prefix", default="📰 Новая публикация:")
    parser.add_argument("--post-suffix", default="")
    parser.add_argument("--disable-preview", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resolve-title", action="store_true", help="Fetch article page to resolve proper title.")
    parser.add_argument("--resolve-summary", action="store_true", help="Fetch article page to resolve short summary.")
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

    posted_count = 0
    for item in items:
        uid = hash_id(item.url or item.title)
        if uid in seen:
            continue

        if args.resolve_title or args.resolve_summary:
            t, s = resolve_from_page(item.url)
            if args.resolve_title and t:
                item.title = t
            if args.resolve_summary and s:
                item.summary = s

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
        time.sleep(1.5)

    save_seen(args.state, seen)
    print(f"Done. Posted {posted_count} new item(s).")


if __name__ == "__main__":
    main()
