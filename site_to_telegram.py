#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
site_to_telegram.py — РулЁжка-стайл (LLM + лимит 1024 символа)

Формат поста:
🚗 **Заголовок**
Интригующее вступление (1–2 строки).
1️⃣ ... 2️⃣ ... 3️⃣ ...
Короткая финальная мысль/совет.
🏎️ РулЁжка (https://t.me/drive_hedgehog)

— без ссылок на источник
— только <b> и <i> в HTML
— caption строго <= 1024 символов (никаких обрезаний троеточием)
"""

import argparse, html, json, os, re, sys, time
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

DEFAULT_UA = "Mozilla/5.0 (compatible; rul-ezhka/2.0)"
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
    "промокод","подробнее","реклам",
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

# ---------------- Helpers ----------------
def fetch_html(url:str)->str:
    r=requests.get(url,headers={"User-Agent":DEFAULT_UA},timeout=25)
    r.raise_for_status()
    return r.text

def normalize_title(title:str)->str:
    t=title.strip()
    # срезаем хвосты вроде «— Autonews», «| Autonews», «Главное :: Autonews»
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
    if image and base_url:
        image=urljoin(base_url,image)

    # body root
    root=None
    for sel in ARTICLE_SELECTORS:
        node=soup.select_one(sel)
        if node:
            root=node;break
    if not root: root=soup

    # drop junk nodes (устойчиво к ошибкам селектора)
    for sel in DROP_SELECTORS:
        try:
            for node in root.select(sel):
                node.decompose()
        except Exception:
            continue

    paras=[]
    for p in root.find_all("p"):
        txt=re.sub(r"\s+"," ",p.get_text(" ",strip=True))
        if txt and not is_junk(txt):
            paras.append(txt)

    if not paras:
        d=soup.find("meta",property="og:description")
        if d and d.get("content"):
            paras=[d["content"].strip()]

    return Item(title=title,url=url,image=image,paras=paras[:12])

def choose_emoji(title:str,text:str)->str:
    s=(title+" "+text).lower()
    for keys,e in EMOJI_MAP:
        if any(k in s for k in keys):
            return e
    return "🚗"

def join_text(paras:List[str],limit:int=900)->str:
    out,cur=[],0
    for p in paras:
        if cur+len(p)>limit: break
        out.append(p);cur+=len(p)+1
        if len(out)>=6: break
    return " ".join(out)

# ---------------- LLM ----------------
def llm_style_post(title:str,text:str)->Optional[str]:
    api=os.getenv("OPENAI_API_KEY","").strip()
    if not api:
        return None
    import json,urllib.request as urlreq
    sys_prompt = (
    "Создай короткий пост (до 1024 символов) для Telegram-канала о машинах. "
    "Пиши живо и эмоционально — как автолюбитель, который делится впечатлениями. "
    "Не используй нумерацию. Начни с яркой фразы, потом расскажи 2–3 факта из текста с эмоцией, "
    "и закончи лёгким советом или выводом. "
    "Выделяй <b>ключевые моменты</b> и <i>интересные детали</i>. "
    "Не вставляй ссылки, не повторяй заголовок, не превышай 1024 символа."
    )
    payload={
        "model":"gpt-4o-mini",
        "temperature":0.35,
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
        t=d["choices"][0]["message"]["content"].strip().replace("\r","")
        # разрешаем только <b>/<i>
        t=html.escape(t).replace("&lt;b&gt;","<b>").replace("&lt;/b&gt;","</b>").replace("&lt;i&gt;","<i>").replace("&lt;/i&gt;","</i>")
        return t[:1024]
    except Exception:
        return None

# ---------------- Fallback ----------------
def fallback_style_post(title:str,text:str)->str:
    # интро: 1–2 предложения
    sents=re.split(r"(?<=[.!?…])\s+",text)
    intro=" ".join(sents[:2]).strip()
    # 3–4 пункта
    pts=sents[2:6]
    emojis=["1️⃣","2️⃣","3️⃣","4️⃣"]
    body=[]
    for i in range(min(len(pts),4)):
        p=re.sub(r"(\d+)",r"<b>\1</b>",pts[i])  # числа жирным
        body.append(emojis[i]+" "+p.strip())
    out = intro + ("\n" if intro and body else "") + "\n".join(body)
    return out[:1024]

# ---------------- Telegram ----------------
def tg_send_photo(token:str,chat_id:str,caption_html:str,photo_url:Optional[str],thread_id:Optional[int]=None)->int:
    data={"chat_id":chat_id,"caption":caption_html,"parse_mode":"HTML"}
    if thread_id is not None: data["message_thread_id"]=thread_id
    if photo_url: data["photo"]=photo_url
    r=requests.post(f"{TELEGRAM_API_BASE}/bot{token}/sendPhoto",data=data,timeout=30)
    r.raise_for_status()
    return r.json()["result"]["message_id"]

def tg_copy(token:str,from_chat_id:str,message_id:int,to_chat_id:str)->None:
    requests.post(f"{TELEGRAM_API_BASE}/bot{token}/copyMessage",
                  data={"from_chat_id":from_chat_id,"message_id":message_id,"chat_id":to_chat_id},
                  timeout=20)

# ---------------- Main ----------------
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--url",required=True)
    ap.add_argument("--item-selector",required=True)
    ap.add_argument("--limit",type=int,default=1)  # одна последняя
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

    # безопасно читаем thread_id
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

    # список ссылок
    listing=fetch_html(a.url)
    links=extract_listing_links(listing,a.base_url,a.item_selector,a.limit)

    for link in links:
        if link in seen: continue
        item=parse_article(link,a.base_url)
        merged=join_text(item.paras,limit=900)

        body=llm_style_post(item.title,merged) or fallback_style_post(item.title,merged)

        # финальная мысль
        closing="Берегите себя на дороге и выбирайте с умом."

        # финальный caption (строго <=1024)
        emoji=choose_emoji(item.title,merged)
        cap=f"{emoji} <b>{html.escape(item.title)}</b>\n\n{body}\n\n{closing}\n\n🏎️ РулЁжка (https://t.me/drive_hedgehog)"
        cap=cap[:1024]

        msg_id=tg_send_photo(token,chat,cap,item.image if a.with_photo else None,th)
        if copy:
            tg_copy(token,chat,msg_id,copy)

        seen.add(link)
        with open(a.state,"w",encoding="utf-8") as f:
            json.dump(sorted(list(seen)),f,ensure_ascii=False,indent=2)

        time.sleep(1.0)

    print("Done.")

if __name__=="__main__":
    main()
