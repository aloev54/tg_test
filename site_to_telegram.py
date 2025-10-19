#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
site_to_telegram.py — РулЁжка-стайл (LLM + анти-реклама + нумерация эмодзи)

Формат поста:
🚗 **Заголовок**
Интригующее вступление (1–2 строки).
1️⃣ ... (факт/деталь, можно с *курсивом* и **жирными акцентами**)
2️⃣ ...
3️⃣ ...
Короткая финальная мысль/совет (1 строка).
🏎️ РулЁжка (https://t.me/drive_hedgehog)

— без ссылок на источник
— без обычных маркеров/точек-• (только 1️⃣ 2️⃣ 3️⃣ ...)
— отправляется как photo+caption (HTML), разрешены <b> и <i>
— поддержка темы (message_thread_id) и копии в канал (copyMessage)
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

DEFAULT_UA = "Mozilla/5.0 (compatible; rul-ezhka/1.8)"
TELEGRAM_API_BASE = "https://api.telegram.org"
STATE_FILE = "autonews_seen_nb.json"

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
    image: Optional[str]
    paras: List[str]  # очищенные абзацы статьи

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

def normalize_title(title: str) -> str:
    """Срезаем хвосты типа 'Главное :: Autonews', '— Autonews', '| Autonews' и т.п."""
    t = title.strip()
    patterns = [
        r"\s*[-–—|:]{1,3}\s*(Главное\s*)?::?\s*Autonews(?:\.ru)?\s*$",
        r"\s*[-–—|:]{1,3}\s*Autonews(?:\.ru)?\s*$",
        r"\s*\|\s*(Главное|Новости)\s*$",
        r"\s*::\s*(Главное|Новости)\s*$",
    ]
    for p in patterns:
        t = re.sub(p, "", t, flags=re.IGNORECASE)
    t = re.split(r"\s[-–—|:]{1,3}\s", t)[0].strip() or t
    return t

def clean_text(t: str) -> str:
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"Читайте также.*$", "", t, flags=re.I)
    return t

def is_junk(t: str) -> bool:
    lt = t.lower()
    if len(lt) < 40: return True
    return any(p in lt for p in DROP_PHRASES)

def parse_article(url: str, base_url: Optional[str]) -> Item:
    html_text = fetch_html(url)
    soup = BeautifulSoup(html_text, "html.parser")

    # title
    title = None
    t = soup.find("meta", property="og:title")
    if t and t.get("content"):
        title = t["content"].strip()
    if not title and soup.title and soup.title.string:
        title = soup.title.string.strip()
    title = normalize_title(title or url)

    # image
    image = None
    im = soup.find("meta", property="og:image")
    if im and im.get("content"):
        image = im["content"].strip()
    if image and base_url:
        image = urljoin(base_url, image)

    # body root
    root = None
    for sel in ARTICLE_SELECTORS:
        node = soup.select_one(sel)
        if node:
            root = node; break
    if not root: root = soup

    # drop junk nodes (устойчиво к ошибкам селекторов)
    for sel in DROP_SELECTORS:
        try:
            for node in root.select(sel):
                node.decompose()
        except Exception:
            continue

    # paragraphs
    paras = []
    for p in root.find_all("p"):
        txt = clean_text(p.get_text(" ", strip=True))
        if txt and not is_junk(txt):
            paras.append(txt)

    if not paras:
        desc = soup.find("meta", property="og:description")
        if desc and desc.get("content"):
            paras = [desc["content"].strip()]

    return Item(title=title, url=url, image=image, paras=paras[:14])

def choose_emoji(title: str, text: str) -> str:
    s = (title + " " + text).lower()
    for keys, e in EMOJI_MAP:
        if any(k in s for k in keys): return e
    return "🚗"

def join_for_llm(paras: List[str], target=900) -> str:
    out, cur = [], 0
    for p in paras:
        if cur + len(p) > target and out: break
        out.append(p); cur += len(p) + 1
        if len(out) >= 6: break
    return " ".join(out)

# ---------------- LLM style (preferred) ----------------
def llm_style_post(title: str, merged_text: str) -> Optional[str]:
    """
    Требуем у модели:
    - 1–2 строки интригующего вступления (без обычных маркеров)
    - затем 3–5 нумерованных блоков с эмодзи 1️⃣ 2️⃣ 3️⃣ 4️⃣ 5️⃣ (НЕ '-', не '•')
    - **жирный** для ключевых моментов, <i>курсив</i> для 4–8 деталей
    - без ссылок и призывов
    - без посторонних HTML, кроме <b> и <i>
    Возвращаем HTML-строку (только <b> и <i> допустимы).
    """
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import json as _json, urllib.request as _url
        sys_prompt = (
            "Ты пишешь пост для автоканала. Стиль: живо, цепляюще, как увлечённый автолюбитель. "
            "Сначала дай 1–2 строки интригующего вступления. Потом 3–5 пронумерованных блоков с эмодзи "
            "1️⃣ 2️⃣ 3️⃣ 4️⃣ 5️⃣. Не используй '-' или '•'. В каждом блоке добавляй цифры/факты/детали, "
            "немного эмоций или сравнения. Ключевые моменты выделяй <b>жирным</b>, интересные детали — <i>курсивом</i> "
            "(в сумме 4–8 курсивных фрагментов). Не вставляй ссылки и призывы. Верни чистый текст без лишних тегов, "
            "кроме <b> и <i>. Заголовок не пиши — только тело."
        )
        user_prompt = f"Заголовок: {title}\n\nТекст для обработки:\n{merged_text}"
        payload = {
            "model": "gpt-4o-mini",
            "temperature": 0.35,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
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
        t = data["choices"][0]["message"]["content"].strip().replace("\r", "")
        # Разрешаем только <b> и <i>
        t = html.escape(t).replace("&lt;b&gt;","<b>").replace("&lt;/b&gt;","</b>").replace("&lt;i&gt;","<i>").replace("&lt;/i&gt;","</i>")
        # Уберём случайные маркеры в начале строк, если вдруг
        t = re.sub(r"^[\-\*•]\s+", "", t, flags=re.M)
        return t
    except Exception:
        return None

# ---------------- Fallback (без LLM) ----------------
def fallback_style_post(title: str, merged_text: str) -> str:
    # интро: первые 1–2 предложения
    sents = re.split(r"(?<=[.!?…])\s+", merged_text)
    intro = " ".join(sents[:2]).strip()
    # 3–4 пункта: берём следующие насыщенные предложениями части
    points = []
    for s in sents[2:]:
        s = s.strip()
        if 50 <= len(s) <= 220:
            # подчёркнем числа/факты
            s = re.sub(r"\b(\d[\d\s.,%]*\d|\d+)\b", r"<b>\1</b>", s)
            # немного курсива
            s = re.sub(r"\b([A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё\-]{6,})\b", r"<i>\1</i>", s, count=1)
            points.append(s)
        if len(points) >= 4:
            break
    if not intro:
        intro = merged_text[:220].rstrip() + "…"
    # Сборка с эмодзи-нумерацией
    out_lines = [intro]
    emojis = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣"]
    for i, pt in enumerate(points, 1):
        out_lines.append(f"{emojis[i-1]} {pt}")
    return "\n".join(out_lines).strip()

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
    ap.add_argument("--limit", type=int, default=1)  # одна последняя новость
    ap.add_argument("--base-url")
    ap.add_argument("--state", default=STATE_FILE)
    ap.add_argument("--with-photo", action="store_true")
    ap.add_argument("--thread-id", type=int)
    ap.add_argument("--copy-to-chat-id")
    args = ap.parse_args()

    token = os.getenv("TELEGRAM_BOT_TOKEN","").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID","").strip()
    if not token or not chat_id:
        print("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID", file=sys.stderr); sys.exit(2)

    thread_id = args.thread_id
    if thread_id is None:
        env_th = os.getenv("TELEGRAM_THREAD_ID","").strip()
        if env_th.isdigit(): thread_id = int(env_th)
    copy_chat = os.getenv("TELEGRAM_COPY_TO_CHAT_ID","").strip() or (args.copy_to_chat_id or "").strip()

    listing = fetch_html(args.url)
    links = extract_listing_links(listing, args.base_url, args.item_selector, args.limit)

    # state
    seen = set()
    if os.path.exists(args.state):
        try:
            seen = set(json.load(open(args.state,"r",encoding="utf-8")))
        except Exception:
            seen = set()

    posted = 0
    for link in links:
        if link in seen:
            continue

        item = parse_article(link, args.base_url)
        merged = join_for_llm(item.paras, target=900)

        body = llm_style_post(item.title, merged) or fallback_style_post(item.title, merged)
        # финальная короткая мысль
        closing = "Берегите себя на дороге и выбирайте с умом."

        # заголовок + тело + мысль + подпись
        emoji = choose_emoji(item.title, merged)
        caption = f"{emoji} <b>{html.escape(item.title)}</b>\n\n{body}\n\n{closing}\n\n🏎️ РулЁжка (https://t.me/drive_hedgehog)"
        # ограничим длину caption до ~1024 для фото
        if len(caption) > 1024:
            caption = caption[:1000].rstrip() + "…\n\n🏎️ РулЁжка (https://t.me/drive_hedgehog)"

        msg_id = tg_send_photo(token, chat_id, caption, item.image if args.with_photo else None, thread_id)
        if copy_chat:
            tg_copy(token, chat_id, msg_id, copy_chat)

        seen.add(link)
        with open(args.state,"w",encoding="utf-8") as f:
            json.dump(sorted(list(seen)), f, ensure_ascii=False, indent=2)

        posted += 1
        time.sleep(1.0)

    print(f"Done. Posted {posted} item(s).")

if __name__ == "__main__":
    main()
