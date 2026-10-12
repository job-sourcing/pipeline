"""S32 wire: 59 licompany watch entries into board_watch/config.json.

Daily lane (tags ["li"]): 18 boards with live measured US postings.
Dormant lane (tags ["li_dormant"]): 41 boards with exact-name entities
and 0 measured rows — the weekly liveness re-probe (flip = the diff
machinery alerts; promotion = tag removal, state continuity kept).
All carry skip_li_corroboration (review P1-1).
"""
import json
from pathlib import Path

ROOT = Path("/home/z/research")
CFG = ROOT / "ingest/data/board_watch/config.json"
cfg = json.loads(CFG.read_text())

DAILY = {
    "vesync": "VeSync", "temu": "Temu", "otterai": "Otter.ai",
    "heygen": "HeyGen", "heytea": "HeyTea", "uniuni": "UniUni",
    "opusclip": "OpusClip", "wyze": "Wyze", "renpho": "Renpho",
    "shokz": "Shokz", "zepp": "Zepp Health", "firework": "Firework",
    "grubmarket": "GrubMarket", "olight": "Olight",
    "aputure": "Aputure", "creality": "Creality",
    "mixue": "Mixue Group", "gptzero": "GPTZero",
}
DORMANT = {
    "govee": "Govee", "vava": "VAVA (Sunvalley Group)",
    "eveenergy": "EVE Energy", "jinkosolar": "JinkoSolar",
    "renogy": "Renogy", "jackery": "Jackery",
    "tigergraph": "TigerGraph", "tdengine": "TDengine",
    "centurygames": "Century Games", "dreo": "Dreo",
    "bluetti": "Bluetti", "zendure": "Zendure", "hollyland": "Hollyland",
    "xreal": "XREAL", "anycubic": "Anycubic", "elegoo": "Elegoo",
    "baseus": "Baseus", "xtransfer": "XTransfer",
    "lianlian": "LianLian Global", "skyworth": "Skyworth",
    "bentoml": "BentoML", "affine": "AFFiNE", "llamaindex": "LlamaIndex",
    "pixverse": "PixVerse", "databend": "Databend", "notta": "Notta",
    "polloai": "Pollo AI", "vidnoz": "Vidnoz", "fotor": "Fotor",
    "bougerv": "BougeRV", "sengled": "Sengled", "moondrop": "Moondrop",
    "sharge": "Sharge", "xgimi": "XGIMI", "torras": "Torras",
    "timekettle": "Timekettle", "zhiyun": "Zhiyun", "aqara": "Aqara",
    "switchbot": "SwitchBot", "petlibro": "Petlibro", "merach": "Merach",
}

existing = {w["label"] for w in cfg["watches"]}
assert not any(f"{k}_us_fulltime" in existing
               for k in list(DAILY) + list(DORMANT)), "collision!"

added = 0
for key, company in DAILY.items():
    cfg["watches"].append({
        "label": f"{key}_us_fulltime",
        "board": f"custom:licompany:{key}",
        "company": company,
        "country": "United States",
        "tags": ["li"],
        "skip_li_corroboration": True,
    })
    added += 1
for key, company in DORMANT.items():
    cfg["watches"].append({
        "label": f"{key}_us_fulltime",
        "board": f"custom:licompany:{key}",
        "company": company,
        "country": "United States",
        "tags": ["li_dormant"],
        "skip_li_corroboration": True,
    })
    added += 1

cfg["watches"].sort(key=lambda w: w["label"])
CFG.write_text(json.dumps(cfg, indent=1, ensure_ascii=False) + "\n")
print(f"added {added} watches; roster = {len(cfg['watches'])}")
# sanity: no dup labels, every licompany board parses
labels = [w["label"] for w in cfg["watches"]]
assert len(labels) == len(set(labels))
li = [w for w in cfg["watches"] if w["board"].startswith("custom:licompany:")]
print(f"licompany watches: {len(li)} "
      f"(daily {sum(1 for w in li if w['tags']==['li'])}, "
      f"dormant {sum(1 for w in li if w['tags']==['li_dormant'])})")
