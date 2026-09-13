# Supabase edge proxy evaluation

_captured: 01:15:32 UTC_

## 1. Health endpoint

- HTTP 200, body: `{"ok": true, "service": "edge-proxy", "version": "1.0.0", "runtime": "supabase-edge", "time": "2026-08-26T01:15:33.247Z", "auth_enabled": true, "cache_db_enabled": false, "log_to_db": false}`

## 2. IP rotation (default fetch mode, 5 sequential ipinfo calls)

- call 1: status=200 ip=3.34.143.165 city=Incheon country=KR elapsed=826ms proxy_timing=199 edge=ap-northeast-2
- call 2: status=200 ip=15.165.160.169 city=Incheon country=KR elapsed=772ms proxy_timing=197 edge=ap-northeast-2
- call 3: status=200 ip=3.36.124.192 city=Incheon country=KR elapsed=698ms proxy_timing=197 edge=ap-northeast-2
- call 4: status=200 ip=3.35.168.110 city=Incheon country=KR elapsed=538ms proxy_timing=199 edge=ap-northeast-2
- call 5: status=200 ip=54.180.145.228 city=Incheon country=KR elapsed=785ms proxy_timing=196 edge=ap-northeast-2

**Distinct IPs observed:** 5 of 5 calls (rotation = ON)


## 3. Region pinning (x-region header → IP city change)

| region | ip | city | country | edge_region | proxy_timing_ms |
|---|---|---|---|---|---|
| us-east-1 | 18.212.235.233 | Ashburn | US | us-east-1 | 45 |
| us-west-2 | 44.251.143.30 | Boardman | US | us-west-2 | 178 |
| eu-west-1 | 34.245.7.71 | Dublin | IE | eu-west-1 | 138 |
| eu-central-1 | 3.79.110.139 | Frankfurt am Main | DE | eu-central-1 | 132 |
| ap-southeast-1 | 54.254.200.185 | Singapore | SG | ap-southeast-1 | 242 |
| ap-northeast-1 | 35.78.105.196 | Tokyo | JP | ap-northeast-1 | 171 |
| sa-east-1 | 15.229.253.78 | São Paulo | BR | sa-east-1 | 158 |
| ca-central-1 | 3.96.159.17 | Montréal | CA | ca-central-1 | 50 |

## 4. JA3 randomization + mode fingerprint difference (tls.peet.ws/api/all)

| call | mode | ja3_hash | ja4 | akamai_h2_fp | ip_seen | elapsed_ms | proxy_timing_ms |
|---|---|---|---|---|---|---|---|
| fetch#1 | fetch | 37aae44a744c5ed289da4aee8fe09b73 | t13d1011h2_61a7ad8aa9b6_3fcd1a44f3e3 | `2:0;4:2097152;5:16384;6:16384|5177345|0|m,s,a,p` | 43.201.105.200:42780 | 1080 | 559 |
| fetch#2 | fetch | 3f3ff06e88e8c9089ebe2ad9aa8b1965 | t13d1011h2_61a7ad8aa9b6_3fcd1a44f3e3 | `2:0;4:2097152;5:16384;6:16384|5177345|0|m,s,a,p` | 52.79.82.122:54964 | 1193 | 575 |
| fetch#3 | fetch | 6b6175e98d962c8eda060c6a6785965e | t13d1011h2_61a7ad8aa9b6_3fcd1a44f3e3 | `2:0;4:2097152;5:16384;6:16384|5177345|0|m,s,a,p` | 3.36.103.185:58916 | 1001 | 571 |
| http2 | http2 | ff978474f8f1a71fb0f5bbfa61e9ff7f | t13d1011h2_61a7ad8aa9b6_3fcd1a44f3e3 | `2:0;4:2097152;5:16384;6:16384|5177345|0|m,s,a,p` | 3.39.232.117:39638 | 1000 | 558 |
| raw | raw | 2d20abd792d610156ce856f30e786be5 | t13d1011h1_61a7ad8aa9b6_3fcd1a44f3e3 | `None` | 13.125.206.169:42364 | 967 | 555 |

**Distinct JA3 hashes across 5 mode calls:** 5 (randomization ON)

## 5. Header leakage (httpbin.org/headers) — fetch vs raw

| mode | traceparent seen? | x-forwarded-for / x-amzn-trace-id | host | user-agent |
|---|---|---|---|---|
| fetch | True | Root=1-6a8e3e50-31b4c2960b6cb9e8468a7c92 | httpbin.org | Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:121.0)  |
| raw | False | Root=1-6a8e3e52-4dc11f1021d3ebf952e4551e | httpbin.org | Mozilla/5.0 (iPad; CPU OS 17_2 like Mac OS X) Appl |

## 6. Distillation (extract=title on example.com)

- HTTP 200, edge=ap-northeast-2, proxy_timing=59ms, total=352ms
- distilled: `{"format": "html", "data": {"title": "Example Domain"}, "meta": {}}`

## 7. POST handling (httpbin.org/post)

- HTTP 405, edge=ap-northeast-2, proxy_timing=929ms
- body first 200: `<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">
<title>405 Method Not Allowed</title>
<h1>Method Not Allowed</h1>
<p>The method is not allowed for the requested URL.</p>
`

## 8. Error / edge-case handling

| target | mode | status | body snippet | elapsed_ms |
|---|---|---|---|---|
| https://httpbin.org/status/500 | raw | 500 | `` | 1225 |
| https://httpbin.org/status/403 | raw | 403 | `` | 996 |
| https://nonexistent.invalid./ | raw | 502 | `{"error":"No DNS records for nonexistent.invalid.","code":"DNS_EMPTY","request_id":"a2830dbe"}` | 345 |
