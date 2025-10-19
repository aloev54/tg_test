#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
site_to_telegram.py — РулЁжка-стайл
Формирует посты в стиле проекта:
- строка с эмодзи в начале;
- жирный заголовок;
- абзац среднего размера;
- 2–4 пункта со значимыми фактами (•);
- ссылка на источник;
- подпись: "🏎️ *РулЁжка* (https://t.me/drive_hedgehog)";
- отправляет фото (og:image) с HTML caption.
Поддержка тем (message_thread_id) и опционального копирования в канал.
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
from typing import List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

DEFAULT_UA = "Mozilla/5.0 (compatible; site2tg/1.4-rulezka)"
TELEGRAM_API_BASE = "https://api.telegram.org"
STATE_FILE = "seen.json"


@dataclass
class Item:
    title: str
    url: str
    summary: Optional[str] = None
    image: Optional[str] = None
    body: Optional[str] = None


def load_seen(path: str) -> set:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_seen(path: str, seen: set) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(list(seen)), f, ensure_ascii=False, indent=2)


def hash_id(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def fetch_html(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": DEFAULT_UA}, timeout=20)
    r.raise_for_status()
    return r.text


def extract_links(html_text: str, base_url: Optional[str], selector: str, limit: int) -> List[str]:
    soup = BeautifulSoup(html_text, "html.parser")
    nodes = soup.select(selector)[:limit]
    links = []
    for n in nodes:
        a = n if n.name == "a" else n.find("a")
        if a and a.get("href"):
            links.append(urljoin(base_url, a["href"]) if base_url else a["href"])
    return list(dict.fromkeys(links))  # remove duplicates


def smart_sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?…])\s+", text)
    return [p.strip() for p in parts if len(p.strip()) > 40]


EMOJI_MAP = [
    (["дтп", "авар", "столкнов"], "🚨"),
    (["штраф", "налог", "пошлин", "утильсбор"], "💸"),
    (["электро", "ev", "батар", "заряд"], "⚡"),
    (["бензин", "дизел", "топлив"], "⛽"),
    (["трасс", "дорог", "ремонт"], "🛣️"),
    (["гонк", "трек", "спорт"], "🏁"),
]


def choose_emoji(title: str, text: str) -> str:
    s = (title + " " + text).lower()
    for keys, e in EMOJI_MAP:
        if any(k in s for k in keys):
            return e
    return "🚗"


def parse_article(url: str, base_url: Optional[str]) -> Item:
    html_text = fetch_html(url)
    soup = BeautifulSoup(html_text, "html.parser")

    title = soup.find("meta", property="og:title")
    title = title["content"].strip() if title else soup.title.string.strip()

    image = soup.find("meta", property="og:image")
    image = image["content"].strip() if image else None
    if image and base_url:
        image = urljoin(base_url, image)

    desc = soup.find("meta", property="og:description")
    summary = desc["content"].strip() if desc else ""

    ps = [p.get_text(" ", strip=True) for p in soup.find_all("p") if len(p.get_text(strip=True)) > 40]
    body = " ".join(ps[:10])
    sentences = smart_sentences(body)
    bullets = sentences[:4]

    return Item(title=title, url=url, summary=summary, image=image, body="\n".join(bullets))


def build_caption(item: Item) -> str:
    emoji = choose_emoji(item.title, (item.summary or "") + (item.body or ""))
    parts = [emoji, f"<b>{html.escape(item.title)}</b>"]

    if item.summary:
        parts.append(html.escape(item.summary))

    if item.body:
        lines = [f"• {html.escape(x)}" for x in item.body.splitlines() if x.strip()]
        parts.append("\n".join(lines))

    parts.append(item.url)
    parts.append("🏎️ *РулЁжка* (https://t.me/drive_hedgehog)")

    caption = "\n\n".join(parts)
    return caption[:1020]  # Telegram caption limit


def send_photo(token: str, chat_id: str, caption: str, photo: Optional[str], thread_id: Optional[int] = None) -> int:
    url = f"{TELEGRAM_API_BASE}/bot{token}/sendPhoto"
    data = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}
    if thread_id:
        data["message_thread_id"] = thread_id
    if photo:
        data["photo"] = photo
    r = requests.post(url, data=data)
    r.raise_for_status()
    return r.json()["result"]["message_id"]


def copy_message(token: str, from_chat: str, msg_id: int, to_chat: str):
    url = f"{TELEGRAM_API_BASE}/bot{token}/copyMessage"
    data = {"from_chat_id": from_chat, "message_id": msg_id, "chat_id": to_chat}
    requests.post(url, data=data, timeout=20)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--item-selector", required=True)
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--base-url")
    ap.add_argument("--state", default=STATE_FILE)
    ap.add_argument("--with-photo", action="store_true")
    ap.add_argument("--thread-id", type=int)
    ap.add_argument("--copy-to-chat-id")
    args = ap.parse_args()

    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    thread_id_env = os.getenv("TELEGRAM_THREAD_ID", "")
    copy_chat = os.getenv("TELEGRAM_COPY_TO_CHAT_ID", "")

    thread_id = args.thread_id or (int(thread_id_env) if thread_id_env.isdigit() else None)

    seen = load_seen(args.state)
    listing_html = fetch_html(args.url)
    links = extract_links(listing_html, args.base_url, args.item_selector, args.limit)

    for link in links:
        uid = hash_id(link)
        if uid in seen:
            continue

        item = parse_article(link, args.base_url)
        caption = build_caption(item)
        msg_id = send_photo(token, chat_id, caption, item.image if args.with_photo else None, thread_id)
        if copy_chat:
            copy_message(token, chat_id, msg_id, copy_chat)

        seen.add(uid)
        save_seen(args.state, seen)
        time.sleep(1.2)


if __name__ == "__main__":
    main()
