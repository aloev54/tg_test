#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_sites.py — запускает site_to_telegram.py последовательно для сайтов из sites.json
"""

import json, subprocess, os, sys
from pathlib import Path

CONF = "sites.json"
SITE_TO_TG = "site_to_telegram.py"

def load_sites(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as e:
        print("Cannot read sites.json:", e)
        return []

def run_site(cfg):
    cmd = [
        sys.executable, SITE_TO_TG,
        "--url", cfg["url"],
        "--item-selector", cfg["item_selector"],
        "--limit", str(cfg.get("limit", 1)),
        "--state", cfg.get("state", "seen.json")
    ]
    if cfg.get("base_url"):
        cmd += ["--base-url", cfg["base_url"]]
    if cfg.get("with_photo"):
        cmd += ["--with-photo"]

    # Передаём per-site опции через окружение (не светим в логах как аргументы)
    env = os.environ.copy()
    if cfg.get("thread_id"):
        env["TELEGRAM_THREAD_ID"] = str(cfg["thread_id"])
    if cfg.get("copy_to_chat_id"):
        env["TELEGRAM_COPY_TO_CHAT_ID"] = str(cfg["copy_to_chat_id"])

    print("Running:", " ".join(cmd))
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    print("Return code:", proc.returncode)
    if proc.stdout:
        print("--- stdout ---\n" + proc.stdout)
    if proc.stderr:
        print("--- stderr ---\n" + proc.stderr)
    return proc.returncode == 0

def main():
    sites = load_sites(CONF)
    if not sites:
        print("No sites found in", CONF); sys.exit(1)
    ok = 0
    for s in sites:
        print("\n=== Site:", s.get("name") or s.get("url"), "===")
        try:
            if run_site(s):
                ok += 1
        except Exception as e:
            print("Error running site:", e)
    print(f"Done. Successful: {ok}/{len(sites)}")

if __name__ == "__main__":
    main()
