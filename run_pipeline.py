#!/usr/bin/env python3
"""
Luarmor v4 + Luraph v15 full deobfuscation pipeline.
Fetches FlowAuth loader -> gets LuArmor init -> fetches x.luarmor.net auth -> devirts.
"""
import os, sys, re, subprocess, tempfile, urllib.request, json, time

LOADER_URL = "https://flowauth.net/v1/loaders/fa91d4951a460fad87e13be003dc2976.lua"
PAYLOAD_FILE = "payload.lua"
ROBLOX_UA = "Roblox/Win32"

def http_get(url, timeout=30, ua=ROBLOX_UA):
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("latin-1")

def step1_fetch_loader():
    print("[1/5] Fetching FlowAuth loader...")
    status, text = http_get(LOADER_URL)
    print(f"      status={status}, size={len(text)}")
    if "_bsdata0" not in text:
        print("ERROR: not a valid LuArmor loader")
        sys.exit(1)
    with open("loader_stub.lua", "w") as f:
        f.write(text)
    print("      saved -> loader_stub.lua")
    return text

def step2_parse_stub(text):
    print("[2/5] Parsing loader stub...")
    sys.path.insert(0, ".")
    from luarmor_crack.loader import parse_stub, parse_bsdata0
    stub = parse_stub(text)
    print(f"      module_id={stub['module_id']}")
    print(f"      init_url={stub['init_url']}")
    return stub

def step3_fetch_init(stub):
    print("[3/5] Fetching sephal init file...")
    init_url = stub["init_url"]
    if not init_url:
        print("ERROR: no init_url in stub")
        sys.exit(1)
    status, text = http_get(init_url)
    print(f"      status={status}, size={len(text)}")
    with open("init_sephal.lua", "w") as f:
        f.write(text)
    print("      saved -> init_sephal.lua")
    return text

def step4_fetch_luarmor_auth(stub):
    print("[4/5] Getting LuArmor auth URL from loader...")
    # parse _bsdata0 to find the auth URL parameters
    from luarmor_crack.loader import parse_bsdata0
    bsdata = parse_bsdata0(stub["bsdata0_line"])
    
    # the auth URL base is bsdata[1] usually
    # try to reconstruct the URL
    module_id = stub["module_id"]
    
    # extract d parameter from bsdata0 (it's the device fingerprint blob)
    # try direct: bsdata0[1] is the host path, bsdata0[2] is the a param
    try:
        host_path = bsdata[1] if isinstance(bsdata[1], str) else None
        a_param = bsdata[2] if isinstance(bsdata[2], str) else None
        d_param = bsdata[3] if isinstance(bsdata[3], (str, bytes)) else None
        
        if isinstance(d_param, bytes):
            d_param = d_param.hex()
        
        print(f"      host_path={repr(host_path)[:60]}")
        print(f"      a_param={repr(a_param)[:40]}")
        print(f"      d_param type={type(d_param)}, len={len(str(d_param)) if d_param else 0}")
        
        if host_path and a_param and d_param:
            # b param is executor fingerprint - we use a known working one from prior capture
            # This is from a real Delta run, may work without b
            auth_url = f"https://{host_path}?a={a_param}&d={d_param}"
            print(f"      trying auth URL (no b param)...")
            try:
                status, resp = http_get(auth_url, timeout=15)
                print(f"      auth response: status={status}, size={len(resp)}")
                print(f"      first 200 bytes: {repr(resp[:200])}")
                with open("luarmor_auth_response.txt", "w") as f:
                    f.write(resp)
                return resp
            except Exception as e:
                print(f"      auth request failed: {e}")
    except Exception as e:
        print(f"      parse error: {e}")
    
    # fallback: dump what we have
    print("      dumping bsdata0 keys...")
    for k, v in list(bsdata.items())[:10]:
        vr = repr(v)[:80] if not isinstance(v, bytes) else v.hex()[:80]
        print(f"        [{k}] = {vr}")
    
    return None

def step5_devirt(init_text):
    print("[5/5] Running Luraph v15 devirt...")
    
    # clone the deobfuscator repo
    print("      cloning Deobfuscator-Luraph-V15...")
    ret = subprocess.run(
        ["git", "clone", "--depth=1",
         "https://github.com/luraph/deobfuscator-luraph-v15",
         "deob_repo"],
        capture_output=True, timeout=60
    )
    if ret.returncode != 0:
        # try alternate name
        ret = subprocess.run(
            ["git", "clone", "--depth=1",
             "https://github.com/LorekeeperZinnia/Deobfuscator-Luraph-V15",
             "deob_repo"],
            capture_output=True, timeout=60
        )
    
    if ret.returncode != 0:
        print(f"      git clone failed: {ret.stderr.decode()[:200]}")
        print("      will use payload.lua directly with harness if available")
        return False
    
    print("      cloned ok")
    
    # find luau binary
    luau = None
    for p in ["deob_repo/bin/luau", "deob_repo/bin/luau.exe"]:
        if os.path.exists(p):
            os.chmod(p, 0o755)
            luau = p
            break
    
    if not luau:
        print("      no luau binary in repo")
        return False
    
    # build harness with payload
    with open(PAYLOAD_FILE, "r", encoding="latin-1") as f:
        payload = f.read()
    
    with open("deob_repo/undefined.harness.luau", "r", encoding="utf-8", errors="replace") as f:
        harness = f.read()
    
    # find safe bracket level
    level = 0
    while True:
        ob = "[" + "="*level + "["
        cb = "]" + "="*level + "]"
        if ob not in payload and cb not in payload:
            break
        level += 1
    
    ob = "[" + "="*level + "["
    cb = "]" + "="*level + "]"
    final = f"__SOURCE = {ob}\n{payload}\n{cb}\n\n" + harness
    
    with open("harness_run.luau", "w", encoding="utf-8") as f:
        f.write(final)
    
    print(f"      harness built ({len(final)} bytes), running devirt...")
    
    result = subprocess.run(
        [luau, "harness_run.luau"],
        capture_output=True, timeout=120
    )
    
    output = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")
    
    with open("devirt_output.lua", "w", encoding="utf-8") as f:
        f.write(output)
        if stderr:
            f.write("\n-- STDERR --\n" + stderr)
    
    print(f"      devirt done: {len(output)} bytes output")
    print(f"      last 500 chars:\n{output[-500:]}")
    return True

if __name__ == "__main__":
    loader_text = step1_fetch_loader()
    stub = step2_parse_stub(loader_text)
    init_text = step3_fetch_init(stub)
    auth_resp = step4_fetch_luarmor_auth(stub)
    step5_devirt(init_text)
    
    print("\n=== DONE ===")
    print("Files produced:")
    for f in ["loader_stub.lua", "init_sephal.lua", "luarmor_auth_response.txt", "devirt_output.lua"]:
        if os.path.exists(f):
            print(f"  {f} ({os.path.getsize(f)} bytes)")
