# curl_cffi install & impersonation verify

_captured: 01:15:19 UTC_

## Install (idempotent)
```
[notice] A new release of pip is available: 25.0.1 -> 26.2.1
[notice] To update, run: pip3 install --upgrade pip
```
- curl_cffi version: `0.16.2`

## Impersonation fingerprint vs local baseline

| client | status | ja3_hash | ja4 | akamai_h2_fp | ip_seen |
|---|---|---|---|---|---|
| urllib baseline | 200 | a1ebe7f90a577e9399eaa60be3c67721 | t13d1713h1_ab0a1bf427ad_89ab6efea773 | None | 47.57.232.232:19048 |
| chrome131 | 200 | b0a436ca4b72d46ae09bb82fd335f8f4 | t13d1516h2_8daaf6152771_02713d6af862 | 1:65536;2:0;4:6291456;6:262144|15663105|0|m,a,s,p | 8.212.10.159:41680 |
| chrome124 | 200 | a83dde16ae1bcf7883014d2ae5de74f7 | t13d1516h2_8daaf6152771_02713d6af862 | 1:65536;2:0;4:6291456;6:262144|15663105|0|m,a,s,p | 47.57.242.119:52734 |
| chrome120 | 200 | 7bfe31db0e96f9ebed4d26070849dfb2 | t13d1516h2_8daaf6152771_02713d6af862 | 1:65536;2:0;4:6291456;6:262144|15663105|0|m,a,s,p | 8.212.10.159:17042 |
| chrome116 | 200 | aab962f4774e519169d9baaf626bce97 | t13d1516h2_8daaf6152771_f37e75b10bcc | 1:65536;2:0;3:1000;4:6291456;6:262144|15663105|0|m,a,s,p | 47.57.242.119:13515 |
| chrome110 | 200 | 636fc337580384b9890b76f4f0e17c95 | t13d1516h2_8daaf6152771_f37e75b10bcc | 1:65536;2:0;3:1000;4:6291456;6:262144|15663105|0|m,a,s,p | 47.57.232.232:59971 |
| chrome107 | 200 | cd08e31494f9531f560d64c695473da9 | t13d1516h2_8daaf6152771_f37e75b10bcc | 1:65536;2:0;3:1000;4:6291456;6:262144|15663105|0|m,a,s,p | 47.57.232.232:44158 |
| safari17_0 | 200 | 773906b0efdefa24a7f2b8eb6985bf37 | t13d2014h2_a09f3c656075_874d27d7ca63 | 2:0;4:4194304;3:100|10485760|0|m,s,p,a | 47.57.232.232:50215 |
| firefox133 | 200 | 2d692a4485ca2f5f2b10ecb2d2909ad3 | t13d1716h2_5b57614c22b0_eeeea6562960 | 1:65536;2:0;4:131072;5:16384|12517377|0|m,p,a,s | 47.57.232.232:7690 |

## Interpretation

- `ja3_hash`/`ja4` DIFFER between curl_cffi impersonate targets AND differ from urllib baseline → real TLS impersonation working.
- `akamai_h2_fp` differs across chrome versions → HTTP/2 fingerprint also impersonated.
- IP should equal local egress IP (HK Alibaba) for all rows — no proxy in play here.