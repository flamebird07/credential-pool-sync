#!/usr/bin/env python3
"""凭证池同步脚本 v3.0 — 使用 hermes auth add CLI 而非直接写 auth.json"""

import json, os, re, subprocess, urllib.request, time, sys
from pathlib import Path

def load_env():
    env_path = Path.home() / '.hermes' / '.env'
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ.setdefault(k.strip(), v.strip())
load_env()

FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")  # MUST be set in env
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")  # MUST be set in env
BASE_TOKEN = "YedtbFYKZatu2QsGti9ch7xbnGc"
TABLE_ID = "tblOSK9HexYVOHBW"

S_UNVERIFIED = "\u23f3 \u672a\u9a8c\u8bc1"
S_OK = "\u2705 \u6b63\u5e38"
S_INVALID = "\u274c \u65e0\u6548"
S_RATE_LIMITED = "\u26a0\ufe0f \u9650\u6d41"

PROVIDER_MAP = {"ARK": "ARK", "longcat": "longcat", "xiaomi": "xiaomi", "Z.AI": "Z.AI"}

def get_feishu_token():
    d = json.dumps({"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}).encode()
    r = urllib.request.Request("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal", data=d, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=10).read())["tenant_access_token"]

def get_records(token):
    h = {"Authorization": f"Bearer {token}"}; a, pt = [], ""
    while True:
        u = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BASE_TOKEN}/tables/{TABLE_ID}/records?page_size=100" + (f"&page_token={pt}" if pt else "")
        r = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=h), timeout=15).read())
        a.extend(r["data"]["items"]); pt = r["data"].get("page_token", "")
        if not r["data"].get("has_more"): break
    return a

def update_status(token, rid, s, n=None):
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    f = {"\u72b6\u6001": s}
    if n: f["\u5907\u6ce8"] = n
    urllib.request.urlopen(urllib.request.Request(f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BASE_TOKEN}/tables/{TABLE_ID}/records/{rid}", data=json.dumps({"fields": f}).encode(), headers=h, method="PUT"), timeout=10)

def extract_text(v):
    if isinstance(v, list) and v: return v[0].get("text", str(v[0]))
    return str(v) if v else ""

def test_key(ak, bu, mn):
    if not ak or not bu: return False, S_INVALID, "\u7f3a\u5c11\u5fc5\u586b"
    bu = bu.rstrip("/"); ia = "anthropic" in bu.lower()
    tm = mn or "deepseek-v4-flash"
    try:
        if ia:
            r = urllib.request.Request(f"{bu}/v1/messages", data=json.dumps({"model": tm, "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]}).encode(), headers={"Content-Type": "application/json", "x-api-key": ak, "anthropic-version": "2023-06-01"})
        else:
            cp = "/chat/completions" if any(bu.endswith(s) for s in ["/v4", "/v3", "/v2"]) else "/v1/chat/completions"
            r = urllib.request.Request(f"{bu}{cp}", data=json.dumps({"model": tm, "max_tokens": 1, "messages": [{"role": "user", "content": "ok"}]}).encode(), headers={"Content-Type": "application/json", "Authorization": f"Bearer {ak}"})
        json.loads(urllib.request.urlopen(r, timeout=15).read()); return True, S_OK, None
    except urllib.error.HTTPError as e:
        eb = e.read().decode("utf-8", errors="replace")[:200]
        if e.code == 401: return False, S_INVALID, "HTTP 401: Key \u65e0\u6548"
        if e.code == 429: return False, S_RATE_LIMITED, "HTTP 429: \u989d\u5ea6\u5df2\u7528\u5b8c"
        if e.code == 400:
            if "unsupported model" in eb.lower() or "not found" in eb.lower(): return True, S_OK, "\u6a21\u578b\u4e0d\u517c\u5bb9\u4f46 Key \u6709\u6548"
            return False, S_INVALID, f"HTTP 400: {eb[:100]}"
        return False, S_INVALID, f"HTTP {e.code}: {eb[:100]}"
    except urllib.error.URLError as e: return False, S_INVALID, f"\u8fde\u63a5\u5931\u8d25: {str(e)[:80]}"
    except Exception as e: return False, S_INVALID, f"\u5f02\u5e38: {str(e)[:80]}"

def auth_add(provider, ak, label=None):
    cmd = ["hermes", "auth", "add", provider, "--type", "api-key", "--api-key", ak]
    if label: cmd.extend(["--label", label])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.returncode == 0, r.stdout.strip() or r.stderr.strip()
    except: return False, "\u8c03\u7528\u5931\u8d25"

def sync():
    print("="*60); print("\u51ed\u8bc1\u6c60\u540c\u6b65 v3.0"); print("="*60)
    tok = get_feishu_token(); rs = get_records(tok)
    print(f"\n\U0001f4cb {len(rs)} \u6761\u8bb0\u5f55")
    for r in rs: update_status(tok, r["record_id"], S_UNVERIFIED)
    print("  \u2705 \u6807\u8bb0\u4e3a \u23f3 \u672a\u9a8c\u8bc1")
    vc, ic = 0, 0
    for r in rs:
        f = r["fields"]; rid = r["record_id"]; pr = extract_text(f.get("Provider","")).strip()
        lb = extract_text(f.get("Label","")).strip(); ak = extract_text(f.get("API Key","")).strip()
        bu = extract_text(f.get("Base URL","")).strip(); mn = extract_text(f.get("\u6a21\u578b","")).strip()
        pn = PROVIDER_MAP.get(pr)
        if not pn: print(f"\n  \u23ed\ufe0f [{lb or pr}] \u8df3\u8fc7: \u672a\u77e5"); update_status(tok, rid, S_INVALID, f"\u672a\u77e5: {pr}"); continue
        if not ak or ak == "***": print(f"\n  \u23ed\ufe0f [{lb or pn}] \u8df3\u8fc7"); update_status(tok, rid, S_INVALID, "\u7f3a\u5c11"); continue
        print(f"\n  \U0001f50d [{lb or pn}] \u9a8c\u8bc1...", end=" "); sys.stdout.flush()
        ok, st, err = test_key(ak, bu, mn)
        if ok:
            print(f"\u2705 {st}"); vc += 1
            cok, cmsg = auth_add(pn, ak, lb or mn)
            if cok:
                update_status(tok, rid, S_OK, f"\u9a8c\u8bc1\u901a\u8fc7 | {mn}" if mn else "\u9a8c\u8bc1\u901a\u8fc7")
                print(f"    \u2795 hermes auth add \u6210\u529f")
            else:
                update_status(tok, rid, S_OK, f"\u6dfb\u52a0\u5931\u8d25: {cmsg[:50]}")
                print(f"    \u26a0\ufe0f \u6dfb\u52a0\u5931\u8d25: {cmsg[:80]}")
        else:
            print(f"\u274c {st}"); ic += 1; update_status(tok, rid, st, err)
        time.sleep(0.3)
    print(f"\n{'='*60}\n\u2705 {vc} \u6709\u6548 | \u274c {ic} \u65e0\u6548\n\u540c\u6b65\u5b8c\u6210 \u2705\n{'='*60}")

if __name__ == "__main__": sync()
