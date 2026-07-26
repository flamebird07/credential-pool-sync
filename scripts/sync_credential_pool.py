#!/usr/bin/env python3
"""凭证池同步脚本 v3.0 — 使用 hermes auth add CLI 而非直接写 auth.json"""

import json, os, re, subprocess, urllib.request, time, sys
import logging
from pathlib import Path
import yaml

def load_env():
    env_path = Path.home() / '.hermes' / '.env'
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.rstrip('\n')
                if not line.lstrip() or line.lstrip().startswith('#'):
                    continue
                if '=' in line:
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

PROVIDER_MAP = {"ARK": "ARK", "longcat": "longcat", "xiaomi": "xiaomi", "Z.AI": "Z.AI", "DeepSeek": "DeepSeek"}
logger = logging.getLogger(__name__)
_last_auth_list_cache = ([], {})

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

def normalize_provider(provider):
    return provider.replace("custom:", "", 1).strip().lower()

def credential_key(provider, label):
    return normalize_provider(provider), label.strip()

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

def sync(skip_rotate=False):
    print("="*60); print("\u51ed\u8bc1\u6c60\u540c\u6b65 v3.0"); print("="*60)
    tok = get_feishu_token(); rs = get_records(tok)
    print(f"\n\U0001f4cb {len(rs)} \u6761\u8bb0\u5f55")
    for r in rs: update_status(tok, r["record_id"], S_UNVERIFIED)
    print("  \u2705 \u6807\u8bb0\u4e3a \u23f3 \u672a\u9a8c\u8bc1")
    records = []
    for original_index, r in enumerate(rs):
        f = r["fields"]; rid = r["record_id"]; pr = extract_text(f.get("Provider","")).strip()
        lb = extract_text(f.get("Label","")).strip(); ak = extract_text(f.get("API Key","")).strip()
        bu = extract_text(f.get("Base URL","")).strip(); mn = extract_text(f.get("\u6a21\u578b","")).strip()
        priority_raw = f.get("优先级", None)
        try:
            priority = int(extract_text(priority_raw)) if priority_raw is not None else 999
        except (ValueError, TypeError):
            priority = 999
        pn = PROVIDER_MAP.get(pr)
        records.append({
            "record_id": rid,
            "provider": pr,
            "provider_name": pn,
            "label": lb,
            "api_key": ak,
            "base_url": bu,
            "model": mn,
            "priority": priority,
            "original_index": original_index,
        })

    deduplicated = {}
    for record in records:
        key = (record["provider"], record["label"])
        existing = deduplicated.get(key)
        if existing is None or record["priority"] < existing["priority"]:
            deduplicated[key] = record
    records = sorted(
        deduplicated.values(),
        key=lambda record: (
            record["provider"],
            record["priority"],
            record["original_index"],
        ),
    )

    removed, remove_failed = remove_stale_credentials(records)
    vc, ic = 0, 0
    for record in records:
        rid = record["record_id"]; pr = record["provider"]; pn = record["provider_name"]
        lb = record["label"]; ak = record["api_key"]; bu = record["base_url"]; mn = record["model"]
        if not pn: print(f"\n  \u23ed\ufe0f [{lb or pr}] \u8df3\u8fc7: \u672a\u77e5"); update_status(tok, rid, S_INVALID, f"\u672a\u77e5: {pr}"); continue
        if not ak or ak == "***": print(f"\n  \u23ed\ufe0f [{lb or pn}] \u8df3\u8fc7"); update_status(tok, rid, S_INVALID, "\u7f3a\u5c11"); continue
        print(f"\n  \U0001f50d [{lb or pn}] \u9a8c\u8bc1...", end=" "); sys.stdout.flush()
        ok, st, err = test_key(ak, bu, mn)
        if ok:
            print(f"\u2705 {st}"); vc += 1
            # 检查是否已存在（通过 hermes auth list 检查相同 label+provider）
            existing = _auth_list()
            already_exists = False
            if existing[1]:  # all_creds
                for cred in existing[1]:
                    # 归一化 provider 名称进行比较（hermes 返回 custom:ark，我们需要比较 ark）
                    cred_provider = normalize_provider(cred["provider"])
                    if cred['label'] == (lb or mn) and cred_provider == pn.lower():
                        already_exists = True
                        break
            
            if already_exists:
                print(f"    ⏭️ 已存在，跳过")
                update_status(tok, rid, S_OK, f"已存在 | {mn}" if mn else "已存在")
                continue
            
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
    check_and_rotate(skip_rotate)
    fallback_removed = cleanup_fallback_chain(records)
    print(f"\n{'='*60}\n\u2705 {vc} \u6709\u6548 | \u274c {ic} \u65e0\u6548 | \U0001f5d1\ufe0f {removed} \u5df2\u5220\u9664 | \u26a0\ufe0f {remove_failed} \u5220\u9664\u5931\u8d25 | \U0001f517 {fallback_removed} fallback \u5df2\u6e05\u7406\n\u540c\u6b65\u5b8c\u6210 \u2705\n{'='*60}")
    print('__RECORDS__' + json.dumps([{'provider': r['provider'], 'provider_name': r['provider_name'], 'label': r['label'], 'model': r['model'], 'base_url': r['base_url'], 'api_key': r['api_key']} for r in records], ensure_ascii=False, default=str))

def cleanup_fallback_chain(feishu_records):
    """Remove fallback entries whose model/base URL pair is absent from Feishu."""
    try:
        result = subprocess.run(
            ["hermes", "fallback", "list"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception as exc:
        logger.warning("hermes fallback list failed: %s", exc)
        return 0
    if result.returncode != 0:
        logger.warning(
            "hermes fallback list failed: %s",
            (result.stderr or result.stdout).strip(),
        )
        return 0

    current_entries = set()
    entry_pattern = re.compile(
        r"^\s*\d+\.\s+(.+?)\s+\(via\s+[^)]+\)\s+\[([^\]]+)\]\s*$"
    )
    for line in result.stdout.splitlines():
        match = entry_pattern.match(line)
        if match:
            current_entries.add(
                (match.group(1).strip().casefold(), match.group(2).rstrip("/").casefold())
            )
    if not current_entries:
        return 0

    expected_entries = {
        (record["model"].strip().casefold(), record["base_url"].rstrip("/").casefold())
        for record in feishu_records
        if record["model"].strip() and record["base_url"].strip()
    }
    stale_entries = current_entries - expected_entries
    if not stale_entries:
        return 0

    hermes_home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    config_path = hermes_home / "config.yaml"
    try:
        with open(config_path, encoding="utf-8") as config_file:
            config = yaml.safe_load(config_file) or {}
        chain = config.get("fallback_providers")
        if not isinstance(chain, list):
            logger.warning("fallback_providers is not a list in %s", config_path)
            return 0

        kept = []
        removed = 0
        for entry in chain:
            if not isinstance(entry, dict):
                kept.append(entry)
                continue
            key = (
                str(entry.get("model", "")).strip().casefold(),
                str(entry.get("base_url", "")).rstrip("/").casefold(),
            )
            if key in stale_entries:
                removed += 1
                print(f"  \U0001f5d1\ufe0f \u6e05\u7406 stale fallback: {entry.get('model')} [{entry.get('base_url')}]")
            else:
                kept.append(entry)
        if not removed:
            return 0

        config["fallback_providers"] = kept
        temp_path = config_path.with_suffix(".yaml.tmp")
        with open(temp_path, "w", encoding="utf-8", newline="\n") as config_file:
            yaml.safe_dump(
                config,
                config_file,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            )
        os.replace(temp_path, config_path)
        return removed
    except Exception as exc:
        logger.warning("failed to clean fallback chain in %s: %s", config_path, exc)
        return 0

def remove_stale_credentials(feishu_records):
    expected = {
        credential_key(r["provider_name"], r["label"] or r["model"])
        for r in feishu_records
        if r["provider_name"]
    }
    _, local_credentials = _auth_list()
    managed_providers = {normalize_provider(provider) for provider in PROVIDER_MAP.values()}
    stale_by_provider = {}
    for cred in local_credentials:
        provider = normalize_provider(cred["provider"])
        if provider not in managed_providers:
            continue
        if credential_key(cred["provider"], cred["label"]) not in expected:
            stale_by_provider.setdefault(cred["provider"], []).append(cred)

    removed, failed = 0, 0
    for provider, credentials in stale_by_provider.items():
        for cred in sorted(credentials, key=lambda item: int(item["idx"]), reverse=True):
            if _remove_cred(provider, cred["idx"]):
                removed += 1
            else:
                failed += 1
    return removed, failed

def check_and_rotate(skip_rotate=False):
    """同步后检测所有 Provider 的活跃凭证健康状态，失效则自动轮转"""
    if skip_rotate:
        return
    providers, all_creds = _auth_list()
    if not providers:
        print("⚠️ 无活跃凭证，尝试同步恢复...")
        run_sync_again()
        return
    
    # 获取飞书表格记录用于健康检查
    tok = get_feishu_token()
    records = get_records(tok)
    
    # 遍历所有 providers 的活跃凭证
    for prov_name, prov_info in providers.items():
        active_cred = prov_info.get('active')
        if not active_cred:
            print(f"⚠️ {prov_name} 无活跃凭证，跳过")
            continue
        
        # 在飞书表格中找到对应的凭证记录
        cred_record = None
        for rec in records:
            fields = rec['fields']
            label = extract_text(fields.get('Label', '')).strip()
            provider = extract_text(fields.get('Provider', '')).strip()
            # hermes 返回 custom:ark，飞书写 ARK，需要归一化
            prov_name_normalized = prov_name.replace('custom:', '', 1).lower()
            if label == active_cred['label'] and provider.lower() == prov_name_normalized:
                cred_record = fields
                break
        
        if not cred_record:
            print(f"⚠️ {prov_name}#{active_cred['label']} 未找到对应记录，跳过")
            continue
        
        # 获取凭证信息
        api_key = extract_text(cred_record.get('API Key', '')).strip()
        base_url = extract_text(cred_record.get('Base URL', '')).strip()
        model = extract_text(cred_record.get('模型', '')).strip()
        
        # 健康检查
        print(f"🔍 检查 {prov_name}#{active_cred['label']} 健康状态...", end=" ")
        ok, status, err = test_key(api_key, base_url, model)
        
        if not ok:
            print(f"❌ {status}")
            # 凭证失效，检查是否可以轮转
            prov_creds = prov_info.get('creds', [])
            if len(prov_creds) > 1:
                print(f"🔄 活跃凭证 {active_cred['label']} 失效，移除中...")
                _remove_cred(prov_name, active_cred['idx'])
                time.sleep(1)
                # 验证切换
                new_providers, _ = _auth_list()
                new_active = new_providers.get(prov_name, {}).get('active')
                if new_active and new_active['label'] != active_cred['label']:
                    print(f"✅ 已切换至: {new_active['label']}")
                else:
                    print("⚠️ 切换未生效，需检查 Provider 凭证池")
            else:
                print(f"⚠️ {prov_name} 仅剩 1 个凭证，无法轮转")
        else:
            print(f"✅ {status}")
    
    print("✅ 所有 Provider 健康检查完成")


def _auth_list():
    """解析 hermes auth list 输出，失败时使用缓存"""
    global _last_auth_list_cache
    r = subprocess.run(["hermes", "auth", "list"], capture_output=True, text=True, timeout=15)
    if r.returncode != 0:
        logger.warning("four-step-enforcer: hermes auth list failed, using cached state")
        return _last_auth_list_cache[1], _last_auth_list_cache[0]
    providers = {}
    all_creds = []
    current_provider = None
    for line in r.stdout.splitlines():
        m = re.match(r"^(\S+)\s+\(\d+ credentials\)", line)
        if m:
            current_provider = m.group(1)
            providers[current_provider] = {'active': None, 'creds': []}
            continue
        m2 = re.match(r"^\s+#(\d+)\s+(.+?)\s+api_key\s+(\S+)\s*(←)?", line)
        if m2 and current_provider:
            idx, label, source = m2.group(1), m2.group(2), m2.group(3)
            is_active = m2.group(4) is not None
            cred_info = {"provider": current_provider, "idx": idx, "label": label, "active": is_active}
            providers[current_provider]['creds'].append(cred_info)
            all_creds.append(cred_info)
            if is_active:
                providers[current_provider]['active'] = cred_info
    _last_auth_list_cache = (all_creds, providers)
    return providers, all_creds


def _remove_cred(provider, idx):
    r = subprocess.run(["hermes", "auth", "remove", provider, str(idx)], capture_output=True, text=True, timeout=15)
    return r.returncode == 0


def run_sync_again():
    """再次执行同步以恢复凭证"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    subprocess.run([sys.executable, os.path.join(script_dir, "sync_credential_pool.py")], capture_output=True, text=True, timeout=120)


if __name__ == "__main__":
    sync("--skip-health-rotate" in sys.argv[1:])
