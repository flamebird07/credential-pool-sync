#!/usr/bin/env python3
import subprocess, sys, os, re

def auth_list():
    r = subprocess.run(["hermes", "auth", "list"], capture_output=True, text=True, timeout=15)
    if r.returncode != 0: return {}, []
    provider = None; providers = {}; all_creds = []
    for line in r.stdout.splitlines():
        m = re.match(r"^(\S+)\s+\(\d+ credentials\)", line)
        if m:
            provider = m.group(1)
            providers[provider] = {"active": None, "creds": []}
            continue
        m2 = re.match(r"^\s+#(\d+)\s+(.+?)\s+api_key\s+(\S+)\s*(←)?", line)
        if m2 and provider:
            idx, label, source = m2.group(1), m2.group(2), m2.group(3)
            is_active = m2.group(4) is not None
            cred_info = {"provider": provider, "idx": idx, "label": label, "active": is_active}
            providers[provider]["creds"].append(cred_info)
            all_creds.append(cred_info)
            if is_active: providers[provider]["active"] = cred_info
    return providers, all_creds

def remove_cred(provider, idx):
    r = subprocess.run(["hermes", "auth", "remove", provider, str(idx)], capture_output=True, text=True, timeout=15)
    return r.returncode == 0

def run_sync():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    r = subprocess.run([sys.executable, os.path.join(script_dir, "sync_credential_pool.py")], capture_output=True, text=True, timeout=120)
    print(r.stdout)
    if r.returncode != 0: print(r.stderr, file=sys.stderr)
    return r.returncode == 0

def main():
    print("="*50)
    print("切换凭证池下一个 API")
    print("="*50)
    
    # Step 1: Sync first to get fresh credentials
    print("同步飞书表格...")
    if not run_sync():
        print("ERROR: 同步失败", file=sys.stderr)
        sys.exit(1)
    
    # Step 2: Find provider with multiple credentials
    providers, all_creds = auth_list()
    if not all_creds:
        print("ERROR: 无法读取凭证池")
        sys.exit(1)
    
    # 任何 Provider 都可轮转：直接使用当前活跃凭证的 Provider
    eligible_providers = [
        (prov, info)
        for prov, info in providers.items()
        if len(info["creds"]) > 1 and info["active"] is not None
    ]
    if not eligible_providers:
        print("ERROR: 无活跃凭证")
        sys.exit(1)

    target_prov, target_info = eligible_providers[0]
    target_creds = target_info["creds"]
    active_cred = target_info["active"]
    
    cp, ci, cl = active_cred["provider"], active_cred["idx"], active_cred["label"]
    print("当前活跃: {} #{} ({})".format(cp, ci, cl))
    
    # Step 3: Remove active credential to trigger switch
    old_label = cl
    print("移除当前凭证 {}...".format(old_label))
    if not remove_cred(cp, ci):
        print("切换失败：无法移除凭证")
        sys.exit(1)
    
    # Step 4: Verify switch actually happened
    print("验证切换结果...")
    import time; time.sleep(1)
    new_providers, _ = auth_list()
    new_active = new_providers.get(target_prov, {}).get("active")
    if new_active and new_active["label"] != old_label:
        print("切换成功！当前活跃: {} ({})".format(new_active["label"], new_active["provider"]))
    else:
        print("切换可能未生效，请手动检查")
        sys.exit(1)
    
    print()
    print("完成")

if __name__ == "__main__": main()
