#!/usr/bin/env python3
"""S28 D-S28-2: flag the feishuhire/adp/paylocity boards + the
CN-company workday tenants as geo_scope="non_cn".

NOT flagged (stay US-scope): the US reference anchors (nvidia, openai,
netflix, anthropic, riotgames — US companies kept for pipeline
validation parity, D-S24) and every other class (kinds not in
_ADAPTER_GEO_SCOPE ignore the flag anyway — the honesty convention).
"""
import json

PATH = "ingest/data/board_watch/config.json"
CFG = json.load(open(PATH, encoding="utf-8"))

CN_WORKDAY = {"gea_us_fulltime", "jd_us_fulltime", "tencent_us_fulltime",
              "popmart_us_fulltime", "beone_us_fulltime",
              "chagee_us_fulltime", "canadiansolar_us_fulltime"}
SITE_KINDS = {"feishuhire", "adp", "paylocity"}

changed, already = [], 0
for w in CFG["watches"]:
    b = w["board"]
    kind = b.split(":")[1] if b.startswith("ats:") else ""
    if (kind in SITE_KINDS or w["label"] in CN_WORKDAY) \
            and not w.get("geo_scope"):
        w["geo_scope"] = "non_cn"
        changed.append(w["label"])
    elif w.get("geo_scope") == "non_cn":
        already += 1

with open(PATH, "w", encoding="utf-8") as f:
    json.dump(CFG, f, indent=2, ensure_ascii=False)
    f.write("\n")

print(f"newly flagged: {len(changed)}")
print(f"already flagged (S27): {already}")
print(f"total non_cn boards: "
      f"{sum(1 for w in CFG['watches'] if w.get('geo_scope') == 'non_cn')}"
      f" of {len(CFG['watches'])}")

