#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
site_to_telegram.py — РулЁжка-стайл (LLM + анти-реклама)

Формат поста:
- первая строка: эмодзи + жирный заголовок
- далее: текст средней длины (без пунктов), с несколькими ключевыми словами в курсиве
- НИКАКИХ ссылок на источник
- подпись: 🏎️ *РулЁжка* (https://t.me/drive_hedgehog)
- отправляем как photo + HTML caption; прикрепляем og:image

Опционально:
- message_thread_id (пост в тему в группе)
- копия в другой чат/канал (copyMessage)
- LLM-полировка при наличии OPENAI_API_KEY
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

DEFAULT_UA = "Mozilla/5.0 (compatible; rul-ezhka/1.6)"
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


@dataclass
class Item:
    title: str
    url: str
    text: str            # готовый текст (с <i>курсивом</i>), без пунктов
    image: Optional[str]


# ---------------- Core helpers ----------------
def fetch_html(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": DEFAULT_UA}, timeout=25)
    r.raise_for_status()
    return r.text


def extract_listing_links(list_html: str, base_url: Optional[str], selector: str, limit: int) -> List[str]:
    soup = BeautifulSoup(list_html, "html.parser")
    nodes = soup.select(selector)[:limit]
    links = []
    for n in nodes:
        a = n if n.name == "a" else n.find("a")
        if a and a.get("href"):
            links.append(urljoin(base_url, a["href"]) if base_url else a["href"])
    # dedupe preserve order
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
    t = re.sub(r"Читайте также.*$", "", t, flags=re.I)
    return t


def is_junk(t: str) -> bool:
    lt = t.lower()
    if len(lt) < 40:
        return True
    return any(p in lt for p in DROP_PHRASES)


def split_sents(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?…])\s+", text)
    return [p.strip() for p in parts if p.strip()]


def medium_text(paras: List[str], target=700) -> str:
    # Собираем 1–3 абзаца средней длины без пунктов
    sents = split_sents(" ".join(paras))
    out, cur = [], 0
    for s in sents:
        if is_junk(s):
            continue
        if cur + len(s) > target and out:
            break
        out.append(s); cur += len(s) + 1
        if len(out) >= 5:
            break
    text = " ".join(out).strip()
    if len(text) > target + 150:
        text = text[:target].rstrip() + "…"
    return text


def parse_article(url: str, base_url: Optional[str]) -> tuple[str, Optional[str], List[str]]:
    """return title, image, clean paragraphs list"""
    html_text = fetch_html(url)
    soup = BeautifulSoup(html_text, "html.parser")

    # title
    title = None
    t = soup.find("meta", property="og:title")
    if t and t.get("content"):
        title = t["content"].strip()
    if not title and soup.title and soup.title.string:
        title = soup.title.string.strip()
    title = title or url

    # image
    image = None
    im = soup.find("meta", property="og:image")
    if im and im.get("content"):
        image = im["content"].strip()
    if image and base_url:
        image = urljoin(base_url, image)

    # body
    root = None
    for sel in ARTICLE_SELECTORS:
        node = soup.select_one(sel)
        if node:
            root = node; break
    if not root:
        root = soup

    # drop junk nodes
    for sel in DROP_SELECTORS:
        for node in root.select(sel):
            node.decompose()

    # collect paragraphs
    paras = []
    for p in root.find_all("p"):
        txt = clean_text(p.get_text(" ", strip=True))
        if txt and not is_junk(txt):
            paras.append(txt)

    # Ensure we have some text
    if not paras:
        desc = soup.find("meta", property="og:description")
        if desc and desc.get("content"):
            paras = [desc["content"].strip()]

    return title, image, paras


def italicize_some_keywords(text: str, max_terms=4) -> str:
    """На случай отсутствия LLM: курсив 2–4 ключевых слов (простая эвристика)."""
    words = re.findall(r"[A-Za-zА-Яа-яЁё0-9\-]{5,}", text)
    stop = set("которые который которая которое также если этого нужно между более очень тогда чтобы через после перед связи своем своем своей своих всего может пока пока любом любом таких такие такая такое будет будут стали столько такой таких этих этим этом".split())
    # Выбираем «часто встречающиеся» и не стоп-слова
    freq = {}
    for w in words:
        lw = w.lower()
        if lw in stop:
            continue
        freq[lw] = freq.get(lw, 0) + 1
    terms = sorted(freq, key=freq.get, reverse=True)[:max_terms]
    # Оборачиваем первые вхождения
    def repl(m):
        w = m.group(0)
        lw = w.lower()
        if lw in terms and not hasattr(repl, "used") or lw not in getattr(repl, "used", set()):
            repl.used = getattr(repl, "used", set()); repl.used.add(lw)
            return f"<i>{w}</i>"
        return w
    return re.sub(r"[A-Za-zА-Яа-яЁё0-9\-]{5,}", repl, text, count=0)


# ---------------- LLM “повар” ----------------
def llm_make_text(title: str, merged_text: str) -> Optional[str]:
    """
    Если задан OPENAI_API_KEY — просим модель:
    - выдать 1–3 абзаца средней длины без пунктов
    - выделить 2–4 ключевых слова КУРСИВОМ с тегами <i>…</i>
    - не вставлять ссылки и призывы
    - без HTML кроме <i>
    """
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import json as _json, urllib.request as _url
        payload = {
            "model": "gpt-4o-mini",
            "temperature": 0.3,
            "messages": [
                {"role": "system", "content": "Ты помогаешь написать краткий автоновостной пост. Формат: 1–3 абзаца средней длины. Без пунктов/списков. Без ссылок. Используй курсив у 2–4 ключевых слов с тегами <i>…</i>. Больше никаких HTML-тегов."},
                {"role": "user", "content": f"Заголовок: {title}\n\nТекст для сжатия и очистки:\n{merged_text}"}
            ]
        }
        req = _url.Request(
            "https://api.openai.com/v1/chat/completions",
            data=_json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with _url.urlopen(req, timeout=25) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        t = data["choices"][0]["message"]["content"].strip()
        # Разрешаем только <i>…</i>, остальное экранируем.
        t = t.replace("\r", "")
        t_esc = html.escape(t)
        t_esc = t_esc.replace("&lt;i&gt;", "<i>").replace("&lt;/i&gt;", "</i>")
        return t_esc
    except Exception:
        return None


# ---------------- Telegram ----------------
def tg_send_photo(token: str, chat_id: str, caption_html: str, photo_url: Optional[str], thread_id: Optional[int] = None) -> int:
    url = f"{TELEGRAM_API_BASE}/bot{token}/sendPhoto"
    data = {"chat_id": chat_id, "caption": caption_html, "parse_mode": "HTML"}
    if thread_id is not None:
        data["message_thread_id"] = thread_id
    if photo_url:
        data["photo"] = photo_url
    r = requests.post(url, data=data, timeout=25)
    r.raise_for_status()
    return r.json()["result"]["message_id"]


def tg_copy(token: str, from_chat: str, msg_id: int, to_chat: str):
    url = f"{TELEGRAM_API_BASE}/bot{token}/copyMessage"
    data = {"from_chat_id": from_chat, "message_id": msg_id, "chat_id": to_chat}
    requests.post(url, data=data, timeout=20)


# ---------------- Main ----------------
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

    # тема/копия
    thread_id = args.thread_id
    if thread_id is None:
        thread_env = os.getenv("TELEGRAM_THREAD_ID", "").strip()
        if thread_env.isdigit():
            thread_id = int(thread_env)
    copy_chat = os.getenv("TELEGRAM_COPY_TO_CHAT_ID", "").strip() or (args.copy_to_chat_id or "").strip()

    # список ссылок
    listing = fetch_html(args.url)
    links = extract_listing_links(listing, args.base_url, args.item_selector, args.limit)

    # state
    seen = set()
    if os.path.exists(args.state):
        try:
            seen = set(json.load(open(args.state, "r", encoding="utf-8")))
        except Exception:
            seen = set()

    posted = 0
    for link in links:
        uid = link  # URL как id
        if uid in seen:
            continue

        title, image, paras = parse_article(link, args.base_url)
        base_text = medium_text(paras, target=700)

        # LLM-повар или локальный курсив
        cooked = llm_make_text(title, base_text) or italicize_some_keywords(base_text)

        # Финальная сборка caption (без ссылки на источник!)
        emoji = choose_emoji(title, base_text)
        cap_parts = [
            f"{emoji} <b>{html.escape(title)}</b>",
            cooked,
            "🏎️ *РулЁжка* (https://t.me/drive_hedgehog)",
        ]
        caption = "\n\n".join([p for p in cap_parts if p]).strip()
        if len(caption) > 1024:
            caption = caption[:1000].rstrip() + "…\n\n🏎️ *РулЁжка* (https://t.me/drive_hedgehog)"

        msg_id = tg_send_photo(token, chat_id, caption, image if args.with_photo else None, thread_id)
        if copy_chat:
            tg_copy(token, chat_id, msg_id, copy_chat)

        seen.add(uid)
        with open(args.state, "w", encoding="utf-8") as f:
            json.dump(sorted(list(seen)), f, ensure_ascii=False, indent=2)

        posted += 1
        time.sleep(1.1)

    print(f"Done. Posted {posted} item(s).")


if __name__ == "__main__":
    main()
