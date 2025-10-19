#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_sites.py — запускает site_to_telegram.py последовательно для сайтов из sites.json
- Реальный стрим логов (без буферизации capture_output)
- Жёсткий таймаут на сайт (по умолчанию 120 сек)
- Аккуратное убийство дочернего процесса при превышении таймаута
"""

import json, os, sys, time, subprocess, shlex
from pathlib import Path

CONF = "sites.json"
SITE_TO_TG = "site_to_telegram.py"
PER_SITE_TIMEOUT_SEC = int(os.getenv("PER_SITE_TIMEOUT_SEC", "120"))

def load_sites(path: str):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[runner] Cannot read {path}: {e}", flush=True)
        return []

def run_site(cfg: dict) -> bool:
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

    env = os.environ.copy()
    # per-site опционально
    if cfg.get("thread_id"):
        env["TELEGRAM_THREAD_ID"] = str(cfg["thread_id"])
    if cfg.get("copy_to_chat_id"):
        env["TELEGRAM_COPY_TO_CHAT_ID"] = str(cfg["copy_to_chat_id"])

    print(f"[runner] Running: {' '.join(shlex.quote(p) for p in cmd)}", flush=True)

    # Стримим логи в реальном времени, чтобы GitHub Actions не решил, что шаг «завис»
    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        universal_newlines=True,
    )

    start = time.time()
    try:
        for line in proc.stdout:
            print(line.rstrip(), flush=True)
            # ручной таймаут
            if time.time() - start > PER_SITE_TIMEOUT_SEC:
                print(f"[runner] Timeout {PER_SITE_TIMEOUT_SEC}s: killing child...", flush=True)
                proc.kill()
                proc.wait(timeout=5)
                return False
        rc = proc.wait()
        print(f"[runner] Return code: {rc}", flush=True)
        return rc == 0
    except Exception as e:
        print(f"[runner] Exception while running site: {e}", flush=True)
        try:
            proc.kill()
        except Exception:
            pass
        return False

def main():
    sites = load_sites(CONF)
    if not sites:
        print(f"[runner] No sites found in {CONF}", flush=True)
        sys.exit(1)

    ok = 0
    for s in sites:
        print("\n[runner] === Site:", (s.get("name") or s.get("url")), "===", flush=True)
        ok |= run_site(s)

    print(f"[runner] Done. Success={bool(ok)}", flush=True)
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
