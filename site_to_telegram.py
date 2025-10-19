#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
site_to_telegram.py — РулЁжка-стайл (LLM + лимит 1024 символа)
"""

import argparse, html, json, os, re, sys, time
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

DEFAULT_UA = "Mozilla/5.0 (compatible; rul-ezhka/1.9)"
TELEGRAM_API_BASE = "https://api.telegram.org"
STATE_FILE = "autonews_seen_nb.json"

ARTICLE_SELECTORS = [
    "article","[itemprop='articleBody']",".article__body",".js-mediator-article",".article",
]
DROP_SELECTORS = [
    "script","style","noscript","[class*='advert']","[class*='ad-']","[class*='ad_']","[id*='ad']",
    "[class*='banner']","[class*='promo']","[class*='subscribe']","[class*='subscription']",
    "[class*='breadcrumbs']","[class*='share']","[class*='social']","[class*='tags']",
    "[class*='related']","[class*='widget']","figure figcaption",".photo-credit",".copyright",
]
DROP_PHRASES = [
    "реклама","подпис","читайте также","смотрите также","наш телеграм","промокод","подробнее","реклам",
]

EMOJI_MAP = [
    (["дтп","авар","столкнов"],"🚨"),
    (["штраф","налог","пошлин","утильсбор"],"💸"),
    (["электро","ev","батар","заряд"],"⚡"),
    (["бензин","дизел","топлив"],"⛽"),
    (["трасс","дорог","ремонт"],"🛣️"),
    (["гонк","спорт"],"🏁"),
]

@dataclass
class Item:
    title: str
    url: str
    image: Optional[str]
    paras: List[str]

# ----------- helpers -----------
def fetch_html(url:str)->str:
    r=requests.get(url,headers={"User-Agent":DEFAULT_UA},timeout=25)
    r.raise_for_status();return r.text

def normalize_title(title:str)->str:
    t=title.strip()
    t=re.sub(r"[-–—|:]{1,3}\s*(Главное|Autonews.*)$","",t,flags=re.I)
    return t.strip()

def extract_listing_links(html_text:str,base_url:Optional[str],selector:str,limit:int)->List[str]:
    soup=BeautifulSoup(html_text,"html.parser")
    nodes=soup.select(selector)[:limit]
    out=[]
    for n in nodes:
        a=n if n.name=="a" else n.find("a")
        if a and a.get("href"):
            href=urljoin(base_url,a["href"]) if base_url else a["href"]
            if href not in out: out.append(href)
    return out

def is_junk(t:str)->bool:
    lt=t.lower()
    if len(lt)<40: return True
    return any(p in lt for p in DROP_PHRASES)

def parse_article(url:str,base_url:Optional[str])->Item:
    soup=BeautifulSoup(fetch_html(url),"html.parser")
    title=soup.find("meta",property="og:title")
    title=normalize_title(title["content"]) if title and title.get("content") else normalize_title(soup.title.string if soup.title else url)
    im=soup.find("meta",property="og:image")
    image=urljoin(base_url,im["content"]) if im and im.get("content") else None
    root=None
    for sel in ARTICLE_SELECTORS:
        n=soup.select_one(sel)
        if n: root=n;break
    if not root: root=soup
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
    return Item(title,url,image,paras[:10])

def choose_emoji(title,text):
    s=(title+text).lower()
    for keys,e in EMOJI_MAP:
        if any(k in s for k in keys): return e
    return "🚗"

def join_text(paras:List[str],limit=900)->str:
    out,cur=[],0
    for p in paras:
        if cur+len(p)>limit: break
        out.append(p);cur+=len(p)
    return " ".join(out)

# ----------- LLM -----------
def llm_style_post(title:str,text:str)->Optional[str]:
    api=os.getenv("OPENAI_API_KEY","").strip()
    if not api: return None
    import json,urllib.request as urlreq
    prompt=(
        "Создай пост до 1024 символов для Telegram-канала об автомобилях. "
        "Стиль: живо и эмоционально, как автолюбитель. "
        "1–2 строки интро, потом 3–4 блока с эмодзи 1️⃣ 2️⃣ 3️⃣ 4️⃣. "
        "Добавляй факты, цифры, эмоции. "
        "Выделяй <b>жирным</b> и <i>курсивом</i>. "
        "Не вставляй ссылки, не превышай 1024 символа, не обрезай текст."
    )
    payload={
        "model":"gpt-4o-mini",
        "temperature":0.4,
        "max_completion_tokens":600,
        "messages":[
            {"role":"system","content":prompt},
            {"role":"user","content":f"Заголовок: {title}\n\nТекст:\n{text}"}
        ]
    }
    req=urlreq.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization":f"Bearer {api}","Content-Type":"application/json"},
        method="POST"
    )
    try:
        with urlreq.urlopen(req,timeout=30) as r:
            d=json.loads(r.read().decode())
        t=d["choices"][0]["message"]["content"].strip()
        t=html.escape(t).replace("&lt;b&gt;","<b>").replace("&lt;/b&gt;","</b>").replace("&lt;i&gt;","<i>").replace("&lt;/i&gt;","</i>")
        return t[:1024]
    except Exception:
        return None

# ----------- fallback -----------
def fallback_style_post(title,text)->str:
    sents=re.split(r"(?<=[.!?])\s+",text)
    intro=" ".join(sents[:2])
    pts=sents[2:6]
    emojis=["1️⃣","2️⃣","3️⃣","4️⃣"]
    body=[]
    for i in range(min(len(pts),4)):
        p=re.sub(r"(\d+)",r"<b>\1</b>",pts[i])
        body.append(emojis[i]+" "+p)
    t=intro+"\n"+"\n".join(body)
    return t[:1024]

# ----------- Telegram -----------
def tg_send_photo(token,chat,caption,photo,thread=None):
    d={"chat_id":chat,"caption":caption,"parse_mode":"HTML"}
    if thread:d["message_thread_id"]=thread
    if photo:d["photo"]=photo
    r=requests.post(f"{TELEGRAM_API_BASE}/bot{token}/sendPhoto",data=d,timeout=30)
    r.raise_for_status();return r.json()["result"]["message_id"]

def tg_copy(token,from_chat,msg,to_chat):
    requests.post(f"{TELEGRAM_API_BASE}/bot{token}/copyMessage",data={
        "from_chat_id":from_chat,"message_id":msg,"chat_id":to_chat
    },timeout=15)

# ----------- main -----------
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

    token=os.getenv("TELEGRAM_BOT_TOKEN","");chat=os.getenv("TELEGRAM_CHAT_ID","")
    if not token or not chat: sys.exit("Need TELEGRAM_BOT_TOKEN and CHAT_ID")
    th=a.thread_id or (int(os.getenv("TELEGRAM_THREAD_ID","0")) or None)
    copy=os.getenv("TELEGRAM_COPY_TO_CHAT_ID","").strip()

    seen=set()
    if os.path.exists(a.state):
        try: seen=set(json.load(open(a.state)))
        except: pass

    html_text=fetch_html(a.url)
    links=extract_listing_links(html_text,a.base_url,a.item_selector,a.limit)
    for link in links:
        if link in seen: continue
        it=parse_article(link,a.base_url)
        merged=join_text(it.paras)
        body=llm_style_post(it.title,merged) or fallback_style_post(it.title,merged)
        emoji=choose_emoji(it.title,merged)
        closing="Берегите себя на дороге и выбирайте с умом."
        caption=f"{emoji} <b>{html.escape(it.title)}</b>\n\n{body}\n\n{closing}\n\n🏎️ РулЁжка (https://t.me/drive_hedgehog)"
        caption=caption[:1024]
        msg=tg_send_photo(token,chat,caption,it.image if a.with_photo else None,th)
        if copy: tg_copy(token,chat,msg,copy)
        seen.add(link)
        json.dump(sorted(list(seen)),open(a.state,"w"),ensure_ascii=False,indent=2)
        time.sleep(1.2)
    print("Done.")

if __name__=="__main__":
    main()
