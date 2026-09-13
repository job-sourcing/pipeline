#!/usr/bin/env python3
"""Local environment inventory + curl_cffi install & verify.
Writes docs/results/inventory.md and docs/results/curl_cffi.md.
"""
from __future__ import annotations
import os, sys, json, time, subprocess, shutil, platform, pathlib, importlib

REPO = pathlib.Path("/home/z/agent-kit")
RESULTS = REPO / "docs" / "results"
RESULTS.mkdir(parents=True, exist_ok=True)

def sh(cmd, timeout=30):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return p.stdout.strip() or p.stderr.strip()
    except Exception as e:
        return f"<err {e!r}>"

def http_get(url, headers=None, timeout=20):
    import urllib.request
    req = urllib.request.Request(url, headers=headers or {"User-Agent":"agent-kit-inventory/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8","ignore")
    except Exception as e:
        return 0, repr(e)

def md_table(rows, headers):
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"]*len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)

inv = ["# Local environment inventory", "", f"_captured: {time.strftime('%Y-%m-%d %H:%M:%S UTC')}_", ""]
inv += ["## Host", ""]
for k in ["uname -a","cat /etc/os-release | head -3","nproc","free -h | head -2",
          "df -h /home /tmp 2>/dev/null | head -5","whoami","id","pwd"]:
    inv.append(f"- `{k}` → `{sh(k)}`")
inv += ["- `sudo -n true` (no-passwd sudo?) → " + sh("sudo -n true 2>&1 && echo YES || echo NO")]
inv += [""]

inv += ["## Toolchain", ""]
tools = ["python3 --version","pip3 --version","git --version","curl --version | head -1",
         "node --version 2>&1","bun --version 2>&1","jq --version 2>&1",
         "which gh google-chrome chromium chromium-browser firefox 2>&1"]
for t in tools:
    inv.append(f"- `{t}` → `{sh(t)}`")
inv += [""]

inv += ["## Python packages already importable", ""]
for pkg in ["requests","urllib3","httpx","aiohttp","playwright","selenium","bs4","lxml","regex","yarl","curl_cffi"]:
    try:
        m = importlib.import_module(pkg)
        v = getattr(m, "__version__", "?")
        inv.append(f"- `{pkg}` ✓ (v{v}, {getattr(m,'__file__','?')})")
    except Exception:
        inv.append(f"- `{pkg}` ✗ not installed")
inv += [""]

inv += ["## Playwright / browser artifacts on disk", ""]
for p in ["/home/z/.cache/ms-playwright","/home/z/.cache/ms-playwright/chromium-*",
          "/usr/bin/google-chrome","/usr/bin/chromium","/opt/google/chrome/chrome"]:
    inv.append(f"- `{p}` → `{sh(f'ls -d {p} 2>/dev/null | head -3')}`")
inv += [""]

inv += ["## Local egress identity", ""]
st, body = http_get("https://ipinfo.io/json")
ipinfo = {}
if st == 200:
    try:
        ipinfo = json.loads(body)
        for k in ["ip","hostname","city","region","country","loc","org","timezone"]:
            inv.append(f"- ipinfo `{k}`: `{ipinfo.get(k)}`")
    except Exception: inv.append(f"- ipinfo raw: `{body[:300]}`")
else:
    inv.append(f"- ipinfo HTTP {st}: `{body[:200]}`")
inv += [""]

inv += ["## Local curl TLS fingerprint (what targets see from the sandbox)", ""]
st, body = http_get("https://tls.peet.ws/api/all")
if st == 200:
    try:
        t = json.loads(body)
        tls = t.get("tls", {}); h2 = t.get("http2", {})
        rows = [["ja3_hash", tls.get("ja3_hash")], ["ja4", tls.get("ja4")],
                ["akamai h2 fp", h2.get("akamai_fingerprint")],
                ["http_version", t.get("http_version")],
                ["user_agent", t.get("user_agent")], ["ip_seen", t.get("ip")]]
        inv.append(md_table(rows, ["field","value"]))
    except Exception as e:
        inv.append(f"- parse error {e!r}; raw: `{body[:400]}`")
else:
    inv.append(f"- tls.peet.ws HTTP {st}: `{body[:200]}`")
inv += [""]

(RESULTS / "inventory.md").write_text("\n".join(inv))
print("wrote docs/results/inventory.md")

# ---- install + verify curl_cffi ----
cc = ["# curl_cffi install & impersonation verify", "", f"_captured: {time.strftime('%H:%M:%S UTC')}_", ""]
cc.append("## Install (idempotent)")
cc.append("```")
cc.append(sh("python3 -m pip install --quiet curl_cffi 2>&1 | tail -3", timeout=180))
cc.append("```")
try:
    import curl_cffi
    cc.append(f"- curl_cffi version: `{curl_cffi.__version__}`")
    from curl_cffi import requests as creq
except Exception as e:
    cc.append(f"- IMPORT FAILED: `{e!r}`")
    (RESULTS/"curl_cffi.md").write_text("\n".join(cc)); sys.exit(1)

cc += ["", "## Impersonation fingerprint vs local baseline", ""]
def peet_chrome(bt):
    started = time.time()
    try:
        r = creq.get("https://tls.peet.ws/api/all", impersonate=bt, timeout=20)
        t = json.loads(r.text); tls=t.get("tls",{}); h2=t.get("http2",{})
        return (bt, r.status_code, tls.get("ja3_hash"), tls.get("ja4"),
                h2.get("akamai_fingerprint"), t.get("ip"), int((time.time()-started)*1000), None)
    except Exception as e:
        return (bt, None, None, None, None, None, int((time.time()-started)*1000), repr(e)[:80])

rows = []
st, body = http_get("https://tls.peet.ws/api/all")
if st == 200:
    t = json.loads(body); tls=t.get("tls",{}); h2=t.get("http2",{})
    rows.append(("urllib baseline", st, tls.get("ja3_hash"), tls.get("ja4"),
                 h2.get("akamai_fingerprint"), t.get("ip"), None, None))
for bt in ["chrome131","chrome124","chrome120","chrome116","chrome110","chrome107","safari17_0","firefox133"]:
    rows.append(peet_chrome(bt))

cc.append(md_table([(r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows],
                   ["client","status","ja3_hash","ja4","akamai_h2_fp","ip_seen"]))
cc += ["", "## Interpretation", ""]
cc.append("- `ja3_hash`/`ja4` DIFFER between curl_cffi impersonate targets AND differ from urllib baseline → real TLS impersonation working.")
cc.append("- `akamai_h2_fp` differs across chrome versions → HTTP/2 fingerprint also impersonated.")
cc.append("- IP should equal local egress IP (HK Alibaba) for all rows — no proxy in play here.")
(RESULTS/"curl_cffi.md").write_text("\n".join(cc))
print("wrote docs/results/curl_cffi.md")
print("\n=== SUMMARY ===")
for r in rows:
    print(f"  {r[0]:<22} status={r[1]} ja3={r[2]} ja4={r[3]} ip={r[5]} err={(r[7] or '')[:50]}")
