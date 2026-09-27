# Local environment inventory

_captured: 2026-08-26 01:15:17 UTC_

## Host

- `uname -a` → `Linux c-6a8e3bff-145d6674-b89861f4af64 5.10.134-013.8.3.kangaroo.al8.x86_64 #1 SMP Fri May 29 08:22:43 UTC 2026 x86_64 GNU/Linux`
- `cat /etc/os-release | head -3` → `PRETTY_NAME="Debian GNU/Linux 13 (trixie)"
NAME="Debian GNU/Linux"
VERSION_ID="13"`
- `nproc` → `2`
- `free -h | head -2` → `total        used        free      shared  buff/cache   available
Mem:           3.9Gi       464Mi       3.2Gi        44Ki       442Mi       3.5Gi`
- `df -h /home /tmp 2>/dev/null | head -5` → `Filesystem                               Size  Used Avail Use% Mounted on
c-6a8e3bff-145d6674-b89861f4af64-rootfs  9.9G  137M  9.2G   2% /
c-6a8e3bff-145d6674-b89861f4af64-rootfs  9.9G  137M  9.2G   2% /`
- `whoami` → `z`
- `id` → `uid=1001(z) gid=1001(z) groups=1001(z)`
- `pwd` → `/home/z/agent-kit`
- `sudo -n true` (no-passwd sudo?) → sudo: a password is required
NO

## Toolchain

- `python3 --version` → `Python 3.12.13`
- `pip3 --version` → `pip 25.0.1 from /home/z/.venv/lib/python3.12/site-packages/pip (python 3.12)`
- `git --version` → `git version 2.47.3`
- `curl --version | head -1` → `curl 8.14.1 (x86_64-pc-linux-gnu) libcurl/8.14.1 OpenSSL/3.5.6 zlib/1.3.1 brotli/1.1.0 zstd/1.5.7 libidn2/2.3.8 libpsl/0.21.2 libssh2/1.11.1 nghttp2/1.64.0 nghttp3/1.8.0 librtmp/2.3 OpenLDAP/2.6.10`
- `node --version 2>&1` → `v24.18.0`
- `bun --version 2>&1` → `1.3.14`
- `jq --version 2>&1` → `jq-1.7`
- `which gh google-chrome chromium chromium-browser firefox 2>&1` → ``

## Python packages already importable

- `requests` ✓ (v2.32.5, /home/z/.venv/lib/python3.12/site-packages/requests/__init__.py)
- `urllib3` ✓ (v2.6.3, /home/z/.venv/lib/python3.12/site-packages/urllib3/__init__.py)
- `httpx` ✓ (v0.28.1, /home/z/.venv/lib/python3.12/site-packages/httpx/__init__.py)
- `aiohttp` ✓ (v3.13.3, /home/z/.venv/lib/python3.12/site-packages/aiohttp/__init__.py)
- `playwright` ✓ (v?, /home/z/.venv/lib/python3.12/site-packages/playwright/__init__.py)
- `selenium` ✗ not installed
- `bs4` ✓ (v4.14.3, /home/z/.venv/lib/python3.12/site-packages/bs4/__init__.py)
- `lxml` ✓ (v6.0.2, /home/z/.venv/lib/python3.12/site-packages/lxml/__init__.py)
- `regex` ✓ (v2026.4.4, /home/z/.venv/lib/python3.12/site-packages/regex/__init__.py)
- `yarl` ✓ (v1.23.0, /home/z/.venv/lib/python3.12/site-packages/yarl/__init__.py)
- `curl_cffi` ✓ (v0.16.2, /home/z/.venv/lib/python3.12/site-packages/curl_cffi/__init__.py)

## Playwright / browser artifacts on disk

- `/home/z/.cache/ms-playwright` → `/home/z/.cache/ms-playwright`
- `/home/z/.cache/ms-playwright/chromium-*` → `/home/z/.cache/ms-playwright/chromium-1200
/home/z/.cache/ms-playwright/chromium-1228`
- `/usr/bin/google-chrome` → ``
- `/usr/bin/chromium` → ``
- `/opt/google/chrome/chrome` → ``

## Local egress identity

- ipinfo `ip`: `47.57.242.119`
- ipinfo `hostname`: `None`
- ipinfo `city`: `Hong Kong`
- ipinfo `region`: `Hong Kong`
- ipinfo `country`: `HK`
- ipinfo `loc`: `22.2783,114.1747`
- ipinfo `org`: `AS45102 Alibaba (US) Technology Co., Ltd.`
- ipinfo `timezone`: `Asia/Hong_Kong`

## Local curl TLS fingerprint (what targets see from the sandbox)

| field | value |
|---|---|
| ja3_hash | a1ebe7f90a577e9399eaa60be3c67721 |
| ja4 | t13d1713h1_ab0a1bf427ad_89ab6efea773 |
| akamai h2 fp | None |
| http_version | HTTP/1.1 |
| user_agent | agent-kit-inventory/1.0 |
| ip_seen | 47.57.232.232:5059 |
