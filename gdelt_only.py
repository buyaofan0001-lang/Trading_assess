"""GDELT 全球新闻单独重跑(带冷却+重试，规避限频)。产出 gdelt_raw.json。"""
import json, time, requests

GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
THEMES = {
    "AI算力/数据中心capex": '"AI data center" (capex OR investment OR buildout)',
    "HBM/高带宽存储":        '("high bandwidth memory" OR HBM)',
    "先进封装/CoWoS":        '("advanced packaging" OR CoWoS)',
    "电网/电力设备":          '("power grid" OR transformer) electricity',
    "光互联/CPO/光模块":     '("co-packaged optics" OR "optical transceiver" OR "optical module")',
    "液冷/数据中心散热":      '"liquid cooling" "data center"',
    "储能/电网侧电池":        '("energy storage" OR "grid scale battery")',
    "铜/有色供需":            '("copper supply" OR "copper demand")',
}

def fetch(q):
    params = {"query": q, "mode": "artlist", "maxrecords": "15",
              "timespan": "48H", "format": "json", "sort": "hybridrel"}
    for attempt in range(4):
        try:
            r = requests.get(GDELT, params=params, timeout=30)
            try:
                return r.json().get("articles", []) or []
            except Exception:
                time.sleep(12)   # 限频，退避
        except Exception:
            time.sleep(10)
    return []

out = {}
for name, q in THEMES.items():
    arts = fetch(q)
    out[name] = {"count": len(arts),
                 "arts": [{"title": a.get("title", ""), "domain": a.get("domain", ""),
                           "url": a.get("url", ""), "date": a.get("seendate", "")} for a in arts[:8]]}
    print(f"{name}: {len(arts)}", flush=True)
    time.sleep(8)

json.dump(out, open("/Users/liuguan1/Documents/github/Trading_assess/gdelt_raw.json", "w"),
          ensure_ascii=False, indent=2)
print("DONE", flush=True)
