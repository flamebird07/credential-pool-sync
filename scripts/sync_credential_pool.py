#!/usr/bin/env python3
"""凭证池同步脚本 v2.1 — 含连通性验证 + 状态回写 + 自动切换"""
import json, os, urllib.request, time
from pathlib import Path

FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "cli_a91ad5ae63385bc9")
FEISHU_APP_SECRET = os.environ["FEISHU_APP_SECRET"]
BASE_TOKEN = "YedtbFYKZatu2QsGti9ch7xbnGc"
TABLE_ID = "tblOSK9HexYVOHBW"
AUTH_JSON = Path.home() / ".hermes" / "auth.json"

S_U = "\u23f3 \u672a\u9a8c\u8bc1"
S_A = "\u2705 \u6b63\u5e38"
S_I = "\u274c \u65e0\u6548"
S_R = "\u26a0\ufe0f \u9650\u6d41"

def gt():
    d = json.dumps({"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}).encode()
    r = urllib.request.Request("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal", data=d, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=10).read())["tenant_access_token"]

def gr(t):
    h = {"Authorization": f"Bearer {t}"}; a, pt = [], ""
    while True:
        u = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BASE_TOKEN}/tables/{TABLE_ID}/records?page_size=100" + (f"&page_token={pt}" if pt else "")
        r = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=h), timeout=15).read())
        a.extend(r["data"]["items"]); pt = r["data"].get("page_token", "")
        if not r["data"].get("has_more"): break
    return a

def us(t, rid, s, n=None):
    h = {"Authorization": f"Bearer {t}", "Content-Type": "application/json"}
    f = {"\u72b6\u6001": s}
    if n: f["\u5907\u6ce8"] = n
    urllib.request.urlopen(urllib.request.Request(f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BASE_TOKEN}/tables/{TABLE_ID}/records/{rid}", data=json.dumps({"fields": f}).encode(), headers=h, method="PUT"), timeout=10)

def tk(p, ak, bu, model_name):
    if not ak or not bu: return False, S_I, "\u7f3a\u5c11\u5fc5\u586b"
    bu = bu.rstrip("/"); ia = "anthropic" in bu.lower() or "longcat" in bu.lower()
    test_model = model_name or "deepseek-v4-flash"
    try:
        if ia:
            r = urllib.request.Request(f"{bu}/v1/messages", data=json.dumps({"model": test_model, "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]}).encode(), headers={"Content-Type": "application/json", "x-api-key": ak, "anthropic-version": "2023-06-01"})
        else:
            r = urllib.request.Request(f"{bu}/v1/chat/completions", data=json.dumps({"model": test_model, "max_tokens": 1, "messages": [{"role": "user", "content": "ok"}]}).encode(), headers={"Content-Type": "application/json", "Authorization": f"Bearer {ak}"})
        json.loads(urllib.request.urlopen(r, timeout=15).read()); return True, S_A, None
    except urllib.error.HTTPError as e:
        eb = e.read().decode("utf-8", errors="replace")[:200]
        if e.code == 401: return False, S_I, "HTTP 401: Key \u65e0\u6548"
        if e.code == 429: return False, S_R, "HTTP 429: \u989d\u5ea6\u5df2\u7528\u5b8c"
        if e.code == 400:
            if "unsupported model" in eb.lower() or "not found" in eb.lower(): return True, S_A, "\u6a21\u578b\u4e0d\u517c\u5bb9\u4f46 Key \u6709\u6548"
            return False, S_I, f"HTTP 400: {eb[:100]}"
        return False, S_I, f"HTTP {e.code}: {eb[:100]}"
    except urllib.error.URLError as e: return False, S_I, f"\u8fde\u63a5\u5931\u8d25: {str(e)[:80]}"
    except Exception as e: return False, S_I, f"\u5f02\u5e38: {str(e)[:80]}"

def sync():
    print("="*50); print("\u51ed\u8bc1\u6c60\u540c\u6b65 v2.1"); print("="*50)
    tok = gt(); rs = gr(tok); print(f"\n\U0001f4cb \u98de\u4e66: {len(rs)} \u6761")
    for r in rs: us(tok, r["record_id"], S_U)
    print("  \u2705 \u6807\u8bb0\u4e3a \u23f3 \u672a\u9a8c\u8bc1")
    fe, vc, ic = {}, 0, 0
    for r in rs:
        f = r["fields"]; rid = r["record_id"]; p = str(f.get("Provider", "") or "").strip()
        l = str(f.get("Label", "") or "").strip(); ak = str(f.get("API Key", "") or "").strip()
        bu = str(f.get("Base URL", "") or "").strip(); m = str(f.get("\u6a21\u578b", "") or "").strip()
        pr = int(f.get("\u4f18\u5148\u7ea7", 0)) if isinstance(f.get("\u4f18\u5148\u7ea7"), (int, float)) else 99
        if not p or not ak or ak == "***": print(f"\n  \u23ed\ufe0f [{l or p}] \u8df3\u8fc7"); us(tok, rid, S_I, "\u7f3a\u5c11\u5fc5\u586b"); continue
        print(f"\n  \U0001f50d [{l or p}] ...", end=" "); iv, s, e = tk(p, ak, bu, m)
        if iv:
            print(f"\u2705 {s}"); vc += 1
            us(tok, rid, s, f"\u9a8c\u8bc1\u901a\u8fc7 | {m}" if m else "\u9a8c\u8bc1\u901a\u8fc7")
            pn = p.lower().replace(".", "-").replace(" ", "-").replace(":", "-")
            if not pn.startswith("custom:"): pn = "custom:" + pn
            eid = f"sync-{(m or l).lower().replace(' ', '-')}"
            fe.setdefault(pn, []).append({"id": eid, "label": l or m, "auth_type": "api_key", "priority": pr, "source": f"manual:{ak[:12]}...", "last_status": "active", "base_url": bu, "request_count": 0, "secret_fingerprint": f"sha256:{eid}"})
        else: print(f"\u274c {s}"); ic += 1; us(tok, rid, s, e)
        time.sleep(0.3)
    print(f"\n{'='*50}\n\u2705 {vc} \u6709\u6548 | \u274c {ic} \u65e0\u6548")
    if fe:
        ex = json.loads(AUTH_JSON.read_text(encoding="utf-8")) if AUTH_JSON.exists() else {"version": 1, "providers": {}, "credential_pool": {}}
        ex["credential_pool"] = {}
        for pv, es in fe.items(): es.sort(key=lambda x: x.get("priority", 99)); ex["credential_pool"][pv] = es
        ex["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        AUTH_JSON.write_text(json.dumps(ex, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n\U0001f4dd auth.json: {list(ex['credential_pool'].keys())}, \u5171 {sum(len(v) for v in ex['credential_pool'].values())} \u4e2a")
    else: print("\n\u26a0\ufe0f \u65e0\u6709\u6548\u51ed\u8bc1")
    print(f"\n{'='*50}\n\u540c\u6b65\u5b8c\u6210 \u2705\n{'='*50}")

if __name__ == "__main__": sync()
