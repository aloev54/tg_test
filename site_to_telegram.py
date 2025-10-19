#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
site_to_telegram.py — РулЁжка-стайл (anti-ads + optional LLM)
Готовит пост:
  • эмодзи по контексту
  • жирный заголовок
  • абзац средней длины (чистим рекламу/«Читайте также» и пр.)
  • 2–4 буллета (•) с ключевыми фактами
  • Источник: Autonews   (без ссылки)
  • подпись: 🏎️ *РулЁжка* (https://t.me/drive_hedgehog)
И шлёт фото (og:image) с HTML-caption.
Поддержка темы (message_thread_id) и копии в канал (copyMessage).
Опционально можно включить LLM-поварёнка: set OPENAI_API_KEY.
"""

import argparse
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

# ---------- Config ----------
DEFAULT_UA = "Mozilla/5.0 (compatible; rul-ezhka/1.5)"
TELEGRAM_API_BASE = "https://api.telegram.org"
STATE_FILE = "seen.json"
ARTICLE_SELECTORS = [
    "article",
    "[itemprop='articleBody']",
    ".article__body",
    ".js-mediator-article",
    ".article",
]
DROP_SELECTORS = [
    "script", "style", "noscript",
    "[class*='advert']", "[class*='ad-']", "[class*='ad_']", "[id*='ad']",
    "[class*='banner']", "[class*='promo']",
    "[class*='subscribe']", "[class*='subscription']",
    "[class*='breadcrumbs']", "[class*='share']", "[class*='social']",
    "[class*='tags']", "[class*='related']", "[class*='widget']",
    "figure figcaption", ".photo-credit", ".copyright",
]
DROP_PHRASES = [
    "реклама", "подписывайтесь", "подпишитесь", "подписка",
    "читайте также", "смотрите также", "узнать больше", "рассылка",
    "наш телеграм", "наш telegram", "instagram", "vk.com", "вконтакте",
    "скачайте приложение", "присоединяйтесь к", "промокод",
]

EMOJI_MAP = [
    (["дтп", "авар", "столкнов"], "🚨"),
    (["штраф", "налог", "пошлин", "утильсбор"], "💸"),
    (["электро", "ev", "батар", "заряд"], "⚡"),
    (["бензин", "дизел", "топлив"], "⛽"),
    (["трасс", "дорог", "ремонт", "мост"], "🛣️"),
    (["тест", "обзор", "тест-драйв"], "🧪"),
    (["гонк", "трек", "ралли", "спорт"], "🏁"),
]

# ---------- Data ----------
@dataclass
class Item:
    title: str
    url: str
    summary: Optional[str] = None
    bullets: Optional[List[str]] = None
    image: Optional[str] = None


# ---------- Helpers ----------
def fetch_html(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": DEFAULT_UA}, timeout=25)
    r.raise_for_status()
    return r.text


def extract_links(list_html: str, base_url: Optional[str], selector: str, limit: int) -> List[str]:
    soup = BeautifulSoup(list_html, "html.parser")
    nodes = soup.select(selector)[:limit]
    links = []
    for n in nodes:
        a = n if n.name == "a" else n.find("a")
        if a and a.get("href"):
            links.append(urljoin(base_url, a["href"]) if base_url else a["href"])
    # dedupe keep order
    seen, out = set(), []
    for u in links:
        if u not in seen:
            out.append(u); seen.add(u)
    return out


def choose_emoji(title: str, text: str) -> str:
    s = (title + " " + text).lower()
    for keys, e in EMOJI_MAP:
        if any(k in s for k in keys):
            return e
    return "🚗"


def clean_text(t: str) -> str:
    t = re.sub(r"\s+", " ", t).strip()
    # kill tracking suffixes
    t = re.sub(r"Читайте также.*$", "", t, flags=re.I)
    return t


def is_junk(t: str) -> bool:
    lt = t.lower()
    if len(lt) < 40:  # очень короткое
        return True
    return any(p in lt for p in DROP_PHRASES)


def split_sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?…])\s+", text)
    parts = [p.strip() for p in parts if p.strip()]
    return parts


def medium_paragraph(text: str, target=550) -> str:
    sents = split_sentences(text)
    out = []
    cur = 0
    for s in sents:
        if cur + len(s) > target and out:
            break
        out.append(s); cur += len(s) + 1
        if len(out) >= 3:
            break
    para = " ".join(out).strip()
    if len(para) > target + 100:
        para = para[:target].rstrip() + "…"
    return para


def pick_bullets(text: str, limit=4) -> List[str]:
    sents = split_sentences(text)
    # фильтр: не мусор, длина разумная
    sents = [s for s in sents if 40 <= len(s) <= 240 and not is_junk(s)]
    return sents[:limit]


def parse_article(url: str, base_url: Optional[str]) -> Item:
    html_text = fetch_html(url)
    soup = BeautifulSoup(html_text, "html.parser")

    # Title
    title = None
    t = soup.find("meta", property="og:title")
    if t and t.get("content"): title = t["content"].strip()
    if not title and soup.title and soup.title.string:
        title = soup.title.string.strip()
    title = title or url

    # Image
    image = None
    im = soup.find("meta", property="og:image")
    if im and im.get("content"): image = im["content"].strip()
    if image and base_url: image = urljoin(base_url, image)

    # Article node
    body_root = None
    for sel in ARTICLE_SELECTORS:
        node = soup.select_one(sel)
        if node:
            body_root = node; break
    if not body_root:
        body_root = soup

    # Drop junk nodes
    for sel in DROP_SELECTORS:
        for node in body_root.select(sel):
            node.decompose()

    # Collect paragraphs
    paras = []
    for p in body_root.find_all("p"):
        txt = clean_text(p.get_text(" ", strip=True))
        if txt and not is_junk(txt):
            paras.append(txt)

    # Compose texts
    raw_text = " ".join(paras[:12])  # ограничим объём
    summary = medium_paragraph(raw_text, target=550)
    bullets = pick_bullets(raw_text, limit=4)

    return Item(title=title, url=url, summary=summary, bullets=bullets, image=image)


# ---------- Optional LLM pass ----------
def llm_refine(summary: str, bullets: List[str]) -> (str, List[str]):
    """
    Если есть OPENAI_API_KEY — прозвоним модель для лёгкой полировки.
    Если ключа нет/что-то не так — вернём исходные тексты.
    """
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return summary, bullets
    try:
        import json as _json
        import urllib.request as _url
        payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "Сделай краткий автоновостной пост: 1 абзац до ~550 символов и 3–4 лаконичных буллета. Без рекламы и призывов подписаться."},
                {"role": "user", "content": f"Абзац:\n{summary}\n\nБуллеты:\n" + "\n".join(f"- {b}" for b in bullets)}
            ],
            "temperature": 0.3,
        }
        req = _url.Request(
            "https://api.openai.com/v1/chat/completions",
            data=_json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with _url.urlopen(req, timeout=20) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        text = data["choices"][0]["message"]["content"]
        # простейший парсер ответа: первая часть до пустой строки — абзац, дальше строки буллетов
        parts = text.strip().split("\n")
        new_summary_lines = []
        new_bullets = []
        section_bullets = False
        for line in parts:
            if not line.strip():
                section_bullets = True
                continue
            if section_bullets or line.strip().startswith(("•", "-", "—")):
                new_bullets.append(line.lstrip("•-— ").strip())
            else:
                new_summary_lines.append(line.strip())
        new_summary = " ".join(new_summary_lines).strip()[:650]
        if not new_bullets:
            new_bullets = bullets
        return (new_summary or summary), (new_bullets[:4] or bullets)
    except Exception:
        return summary, bullets


# ---------- Telegram ----------
def send_photo(token: str, chat_id: str, caption: str, photo: Optional[str], thread_id: Optional[int] = None) -> int:
    url = f"{TELEGRAM_API_BASE}/bot{token}/sendPhoto"
    data = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}
    if thread_id is not None:
        data["message_thread_id"] = thread_id
    if photo:
        data["photo"] = photo
    r = requests.post(url, data=data, timeout=25)
    r.raise_for_status()
    return r.json()["result"]["message_id"]


def copy_message(token: str, from_chat: str, msg_id: int, to_chat: str):
    url = f"{TELEGRAM_API_BASE}/bot{token}/copyMessage"
    data = {"from_chat_id": from_chat, "message_id": msg_id, "chat_id": to_chat}
    requests.post(url, data=data, timeout=20)


# ---------- Main ----------
def load_seen(path: str) -> set:
    if os.path.exists(path):
        try:
            return set(json.load(open(path, "r", encoding="utf-8")))
        except Exception:
            return set()
    return set()


def save_seen(path: str, seen: set) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(list(seen)), f, ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--item-selector", required=True)
    ap.add_argument("--limit", type=int, default=6)
    ap.add_argument("--base-url")
    ap.add_argument("--state", default=STATE_FILE)
    ap.add_argument("--with-photo", action="store_true")
    ap.add_argument("--thread-id", type=int)
    ap.add_argument("--copy-to-chat-id")
    args = ap.parse_args()

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID", file=sys.stderr); sys.exit(2)

    thread_env = os.getenv("TELEGRAM_THREAD_ID", "").strip()
    thread_id = args.thread-id if hasattr(args, "thread-id") else None  # safety for argparse
    if thread_id is None and thread_env.isdigit():
        thread_id = int(thread_env)

    copy_chat = os.getenv("TELEGRAM_COPY_TO_CHAT_ID", "").strip() or (args.copy_to_chat_id or "").strip()

    # fetch listing
    listing_html = fetch_html(args.url)
    links = extract_links(listing_html, args.base_url, args.item_selector, args.limit)

    seen = load_seen(args.state)
    posted = 0
    for url in links:
        uid = html.escape(url)
        if uid in seen:
            continue

        item = parse_article(url, args.base_url)

        # optional LLM refine
        item.summary, item.bullets = llm_refine(item.summary or "", item.bullets or [])

        # Build caption (без ссылки на сайт!)
        emoji = choose_emoji(item.title, (item.summary or "") + " " + " ".join(item.bullets or []))
        parts = [emoji, f"<b>{html.escape(item.title)}</b>"]
        if item.summary:
            parts.append(html.escape(item.summary))
        if item.bullets:
            parts.append("\n".join("• " + html.escape(b) for b in item.bullets if b.strip()))
        parts.append("Источник: Autonews")
        parts.append("🏎️ *РулЁжка* (https://t.me/drive_hedgehog)")
        caption = "\n\n".join(parts)
        if len(caption) > 1020:
            caption = caption[:1000].rstrip() + "…\n\nИсточник: Autonews\n\n🏎️ *РулЁжка* (https://t.me/drive_hedgehog)"

        msg_id = send_photo(token, chat_id, caption, item.image if args.with_photo else None, thread_id)
        if copy_chat:
            copy_message(token, chat_id, msg_id, copy_chat)

        seen.add(uid)
        save_seen(args.state, seen)
        posted += 1
        time.sleep(1.1)

    print(f"Done. Posted {posted} item(s).")


if __name__ == "__main__":
    main()
