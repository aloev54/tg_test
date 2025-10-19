#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
site_to_telegram.py — РулЁжка-стайл (LLM, фото и текст отдельными сообщениями)

Правила:
• Пиши живо и с увлечением — как автолюбитель.
• Начинай с интригующего вступления.
• Дальше — цифры/факты/детали, но без сухости; немного эмоций/сравнений.
• Формат: Markdown-стиль (**жирный**, *курсив*), но отправка в HTML (<b>, <i>).
• Эмодзи-нумерация 1️⃣ 2️⃣ 3️⃣ — только для перечислений, без обычных маркеров.
• В конце — короткая финальная мысль (без слова «вывод»).
• Источники не вставлять.
• Фото и текст — ОТДЕЛЬНЫМИ сообщениями.
• Подпись внизу текста: 🏎️ <a href="https://t.me/drive_hedgehog">РулЁжка</a>

Поддержка:
• message_thread_id (темы в группе)
• копия в другой чат/канал (копируем и фото, и текст)
"""

import argparse, html, json, os, re, sys, time
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

DEFAULT_UA = "Mozilla/5.0 (compatible; rul-ezhka/2.1)"
TELEGRAM_API_BASE = "https://api.telegram.org"
STATE_FILE = "autonews_seen_nb.json"

ARTICLE_SELECTORS = [
    "article","[itemprop='articleBody']",".article__body",".js-mediator-article",".article",
]
DROP_SELECTORS = [
    "script","style","noscript",
    "[class*='advert']","[class*='ad-']","[class*='ad_']","[id*='ad']",
    "[class*='banner']","[class*='promo']",
    "[class*='subscribe']","[class*='subscription']",
    "[class*='breadcrumbs']","[class*='share']","[class*='social']",
    "[class*='tags']","[class*='related']","[class*='widget']",
    "figure figcaption",".photo-credit",".copyright",
]
DROP_PHRASES = [
    "реклама","подпис","читайте также","смотрите также","наш телеграм",
    "промокод","подробнее","реклам","узнать больше","скачайте приложение",
]

EMOJI_MAP = [
    (["дтп","авар","столкнов"],"🚨"),
    (["штраф","налог","пошлин","утильсбор"],"💸"),
    (["электро","ev","батар","заряд"],"⚡"),
    (["бензин","дизел","топлив"],"⛽"),
    (["трасс","дорог","ремонт","мост"],"🛣️"),
    (["гонк","спорт","ралли","трек"],"🏁"),
]

@dataclass
class Item:
    title: str
    url: str
    image: Optional[str]
    paras: List[str]

# ---------- fetch & parse ----------
def fetch_html(url:str)->str:
    r=requests.get(url,headers={"User-Agent":DEFAULT_UA},timeout=25)
    r.raise_for_status()
    return r.text

def normalize_title(title:str)->str:
    t=title.strip()
    patterns=[
        r"\s*[-–—|:]{1,3}\s*(Главное\s*)?::?\s*Autonews(?:\.ru)?\s*$",
        r"\s*[-–—|:]{1,3}\s*Autonews(?:\.ru)?\s*$",
        r"\s*\|\s*(Главное|Новости)\s*$",
        r"\s*::\s*(Главное|Новости)\s*$",
    ]
    for p in patterns:
        t=re.sub(p,"",t,flags=re.IGNORECASE)
    t=re.split(r"\s[-–—|:]{1,3}\s",t)[0].strip() or t
    return t

def extract_listing_links(html_text:str,base_url:Optional[str],selector:str,limit:int)->List[str]:
    soup=BeautifulSoup(html_text,"html.parser")
    nodes=soup.select(selector)[:limit]
    out=[]
    for n in nodes:
        a=n if n.name=="a" else n.find("a")
        if a and a.get("href"):
            href=urljoin(base_url,a["href"]) if base_url else a["href"]
            if href not in out:
                out.append(href)
    return out

def is_junk(t:str)->bool:
    lt=t.lower()
    if len(lt)<40: return True
    return any(p in lt for p in DROP_PHRASES)

def parse_article(url:str,base_url:Optional[str])->Item:
    soup=BeautifulSoup(fetch_html(url),"html.parser")

    # title
    t=soup.find("meta",property="og:title")
    title=t["content"].strip() if t and t.get("content") else (soup.title.string.strip() if soup.title else url)
    title=normalize_title(title)

    # image
    im=soup.find("meta",property="og:image")
    image=im["content"].strip() if im and im.get("content") else None
    if image and base_url: image=urljoin(base_url,image)

    # article body
    root=None
    for sel in ARTICLE_SELECTORS:
        node=soup.select_one(sel)
        if node: root=node; break
    if not root: root=soup

    # drop junk safely
    for sel in DROP_SELECTORS:
        try:
            for node in root.select(sel): node.decompose()
        except Exception: continue

    paras=[]
    for p in root.find_all("p"):
        txt=re.sub(r"\s+"," ",p.get_text(" ",strip=True))
        if txt and not is_junk(txt): paras.append(txt)

    if not paras:
        d=soup.find("meta",property="og:description")
        if d and d.get("content"): paras=[d["content"].strip()]

    return Item(title=title,url=url,image=image,paras=paras[:14])

# ---------- formatting helpers ----------
MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
MD_ITAL = re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)")

def markdown_to_html(md:str)->str:
    """Простой конверт: **bold** и *italic* -> <b>, <i>. Экранируем остальное."""
    # Сначала экранируем всё
    esc = html.escape(md)
    # Вернём нужные теги
    esc = MD_BOLD.sub(r"<b>\1</b>", esc)
    esc = MD_ITAL.sub(r"<i>\1</i>", esc)
    return esc

def strip_links(text:str)->str:
    # вычищаем любые URLы, чтобы модель не вставляла ссылки
    return re.sub(r"https?://\S+", "", text)

def sanitize_bullets(text:str)->str:
    # уберём обычные маркеры '-' и '•' в начале строк
    return re.sub(r"^[\-\*•]\s+", "", text, flags=re.M)

def choose_emoji(title:str,text:str)->str:
    s=(title+" "+text).lower()
    for keys,e in EMOJI_MAP:
        if any(k in s for k in keys): return e
    return "🚗"

def join_text(paras:List[str],limit:int=1200)->str:
    out,cur=[],0
    for p in paras:
        if cur+len(p)>limit and out: break
        out.append(p);cur+=len(p)+1
        if len(out)>=7: break
    return " ".join(out)

# ---------- LLM ----------
def llm_style_post(title:str,text:str)->Optional[str]:
    """
    Генерим тело поста (без заголовка). Markdown-стиль: **жирный**, *курсив*.
    Эмодзи-нумерация 1️⃣ 2️⃣ 3️⃣ — только для перечислений. Без ссылок.
    """
    api=os.getenv("OPENAI_API_KEY","").strip()
    if not api: return None
    import json,urllib.request as urlreq
    sys_prompt=(
        "Ты создаёшь пост для Telegram-канала об автомобилях. "
        "Пиши живо и с увлечением, как автолюбитель, а не журналист. "
        "Сначала 1–2 строки интригующего вступления, затем 3–5 пунктов фактов/деталей "
        "для пунктов используй эмодзи-нумерацию 1️⃣ 2️⃣ 3️⃣ (ТОЛЬКО ДЛЯ ПУНКТОВ, ОСТАЛЬНОЕ ПРОСТО ТЕКСТ!!!). "
        "Добавляй цифры и детали, но без сухости; немного эмоций, сравнений или метафор. "
        "Используй Markdown: **жирный** для ключевых моментов, *курсив* для интересных деталей. "
        "Не используй обычные маркеры '-', '•'. Ссылки не вставляй. "
        "Заверши короткой финальной мыслью/советом без слова «вывод». "
        "Возвращай ТОЛЬКО текст поста без заголовка."
    )
    payload={
        "model":"gpt-4o-mini",
        "temperature":0.4,
        "messages":[
            {"role":"system","content":sys_prompt},
            {"role":"user","content":f"Заголовок: {title}\n\nТекст для основы:\n{text}"}
        ]
    }
    req=urlreq.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization":f"Bearer {api}","Content-Type":"application/json"},
        method="POST",
    )
    try:
        with urlreq.urlopen(req,timeout=30) as resp:
            d=json.loads(resp.read().decode("utf-8"))
        md = d["choices"][0]["message"]["content"].strip().replace("\r","")
        md = strip_links(md)
        md = sanitize_bullets(md)
        return markdown_to_html(md)
    except Exception:
        return None

# ---------- Telegram ----------
def tg_send_photo(token:str,chat_id:str,photo_url:str,thread_id:Optional[int]=None)->int:
    data={"chat_id":chat_id,"photo":photo_url}
    if thread_id is not None: data["message_thread_id"]=thread_id
    r=requests.post(f"{TELEGRAM_API_BASE}/bot{token}/sendPhoto",data=data,timeout=30)
    r.raise_for_status()
    return r.json()["result"]["message_id"]

def tg_send_text(token:str,chat_id:str,text_html:str,thread_id:Optional[int]=None)->int:
    data={"chat_id":chat_id,"text":text_html,"parse_mode":"HTML","disable_web_page_preview":True}
    if thread_id is not None: data["message_thread_id"]=thread_id
    r=requests.post(f"{TELEGRAM_API_BASE}/bot{token}/sendMessage",data=data,timeout=30)
    r.raise_for_status()
    return r.json()["result"]["message_id"]

def tg_copy(token:str,from_chat_id:str,message_id:int,to_chat_id:str)->None:
    requests.post(f"{TELEGRAM_API_BASE}/bot{token}/copyMessage",
                  data={"from_chat_id":from_chat_id,"message_id":message_id,"chat_id":to_chat_id},
                  timeout=20)

# ---------- Main ----------
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--url",required=True)
    ap.add_argument("--item-selector",required=True)
    ap.add_argument("--limit",type=int,default=1)
    ap.add_argument("--base-url")
    ap.add_argument("--state",default=STATE_FILE)
    ap.add_argument("--with-photo",action="store_true")
    ap.add_argument("--thread-id",type=int)
    ap.add_argument("--copy-to-chat-id")
    a=ap.parse_args()

    token=os.getenv("TELEGRAM_BOT_TOKEN","").strip()
    chat=os.getenv("TELEGRAM_CHAT_ID","").strip()
    if not token or not chat:
        sys.exit("Need TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")

    env_th=os.getenv("TELEGRAM_THREAD_ID","").strip()
    if a.thread_id is not None:
        th=a.thread_id
    elif env_th.isdigit():
        th=int(env_th)
    else:
        th=None

    copy=os.getenv("TELEGRAM_COPY_TO_CHAT_ID","").strip() or (a.copy_to_chat_id or "").strip()

    # state
    seen=set()
    if os.path.exists(a.state):
        try:
            with open(a.state,"r",encoding="utf-8") as f:
                seen=set(json.load(f))
        except Exception:
            seen=set()

    # listing
    listing=fetch_html(a.url)
    links=extract_listing_links(listing,a.base_url,a.item_selector,a.limit)

    for link in links:
        if link in seen: continue
        item=parse_article(link,a.base_url)
        merged=join_text(item.paras,limit=1200)

        # текст от LLM или fallback-простой склейки без маркеров
        body_html = llm_style_post(item.title, merged)
        if not body_html:
            # fallback: интро + 1️⃣2️⃣3️⃣ из первых предложений
            sents=re.split(r"(?<=[.!?…])\s+", merged)
            intro=" ".join(sents[:2]).strip()
            pts=[s for s in sents[2:] if 50 <= len(s) <= 220][:4]
            emojis=["1️⃣","2️⃣","3️⃣","4️⃣"]
            md = intro + ("\n" if intro and pts else "") + "\n".join(f"{emojis[i]} {pts[i]}" for i in range(len(pts)))
            md = sanitize_bullets(strip_links(md))
            body_html = markdown_to_html(md)

        # финальная сборка текста (ЗАГОЛОВОК + тело + подпись)
        emoji=choose_emoji(item.title, merged)
        header = f"{emoji} <b>{html.escape(item.title)}</b>"
        signature = "🏎️ <a href=\"https://t.me/drive_hedgehog\">РулЁжка</a>"
        text_html = f"{header}\n\n{body_html}\n\n{signature}"

        # отправляем ОТДЕЛЬНО: фото (без caption) → текст
        msg_photo_id = None
        if a.with_photo and item.image:
            msg_photo_id = tg_send_photo(token, chat, item.image, th)
        msg_text_id = tg_send_text(token, chat, text_html, th)

        # копируем при необходимости (и фото, и текст)
        if copy:
            if msg_photo_id: tg_copy(token, chat, msg_photo_id, copy)
            tg_copy(token, chat, msg_text_id, copy)

        # state
        seen.add(link)
        with open(a.state,"w",encoding="utf-8") as f:
            json.dump(sorted(list(seen)),f,ensure_ascii=False,indent=2)

        time.sleep(1.0)

    print("Done.")

if __name__=="__main__":
    main()
