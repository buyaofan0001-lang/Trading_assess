"""
全球新闻层·晨报 v1  (每日可跑)
数据源(全部免费/已有，无需 key)：
  1) 海外风向标个股新闻 —— Yahoo Finance RSS，覆盖驱动 A 股各主线的美股龙头
  2) 国内券商研报方向 —— tushare report_rc（首次覆盖=新方向 + 标题关键词热度）
产出：news_digest_raw.json，并打印摘要供二次提炼(中美映射)。
注：GDELT 也可做全球源，但免费层限频严(1次/5秒)，仅适合每日单次轻量调用，调试不便，故主用 Yahoo。
"""
import sys, json, time, collections, requests
import xml.etree.ElementTree as ET
sys.path.insert(0, '/Users/liuguan1/Documents/github/Trading_assess')
from tushare_client import get_pro

# 海外龙头 → A股主线映射
BELLWETHERS = {
    "NVDA": "AI算力/GPU → A股算力链",
    "AVGO": "CPO/光互联/ASIC(博通) → A股光模块/PCB",
    "MU":   "存储/HBM(美光) → A股存储链(兆易/江波龙)",
    "TSM":  "半导体代工/先进封装(台积电) → A股封测/设备",
    "VRT":  "数据中心电力/液冷(Vertiv) → A股温控/电源(英维克)",
    "ANET": "数据中心网络/光互联(Arista) → A股光模块/铜缆",
    "SMCI": "AI服务器(超微) → A股服务器(工业富联)",
    "FCX":  "铜(自由港) → A股铜链(紫金)",
}
HOTWORDS = ["HBM", "DRAM", "memory", "liquid cooling", "cooling", "server", "switching",
            "Ethernet", "copper", "capex", "earnings", "guidance", "data center",
            "packaging", "tariff", "backlog", "order"]

def yahoo(ticker):
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.content)
        return [{"title": it.findtext("title", "") or "", "date": it.findtext("pubDate", "") or "",
                 "link": it.findtext("link", "") or ""} for it in root.findall(".//item")]
    except Exception as e:
        return [{"title": "ERR:" + str(e)[:60], "date": "", "link": ""}]

out = {"global": {}, "report_rc": {}}
print("=== 海外风向标新闻 (Yahoo Finance RSS) ===")
hot = collections.Counter()
for t, theme in BELLWETHERS.items():
    items = yahoo(t)
    out["global"][t] = {"theme": theme, "items": items[:10]}
    print(f"\n## {t} · {theme}  ({len(items)}条)")
    for it in items[:5]:
        print(f"  - {it['title'][:96]}")
        for w in HOTWORDS:
            if w.lower() in it["title"].lower():
                hot[w] += 1
    time.sleep(1)
print("\n-- 海外标题热词(方向热度) --")
for w, c in hot.most_common(12):
    print(f"  {w}: {c}")

print("\n\n=== 国内券商研报方向 (report_rc) ===")
pro = get_pro()
rows = []
# 注：日期为演示写死，做成每日任务时应改为动态(今日及前1-2交易日)
for d in ["20260618", "20260619"]:
    for attempt in range(3):           # tushare 代理偶发 RemoteDisconnected，重试
        try:
            df = pro.report_rc(report_date=d)
            if df is not None and len(df):
                for _, r in df.iterrows():
                    rows.append({"date": d, "name": r.get("name"), "title": str(r.get("report_title", "")),
                                 "classify": str(r.get("classify", "")), "org": str(r.get("org_name", ""))})
            break
        except Exception as e:
            if attempt == 2:
                print("report_rc err", d, str(e)[:70])
            time.sleep(3)
seen = set(); uniq = []
for t in rows:
    k = (t["name"], t["title"])
    if k not in seen:
        seen.add(k); uniq.append(t)
rows = uniq
out["report_rc"]["rows"] = rows
print(f"研报条数(去重): {len(rows)}")
firsts = [t for t in rows if "首次" in t["classify"]]
print(f"\n-- 首次覆盖(新方向信号) {len(firsts)}条 --")
for t in firsts[:25]:
    print(f"  - {t['name']}: {t['title'][:62]}")
kw = ["算力", "AI", "存储", "HBM", "封装", "光模块", "CPO", "海缆", "电网", "变压器", "储能",
      "逆变", "液冷", "散热", "温控", "机器人", "铜", "半导体", "设备", "数据中心", "电力", "PCB", "服务器"]
cnt = collections.Counter()
for t in rows:
    for k in kw:
        if k in t["title"]:
            cnt[k] += 1
print("\n-- 研报标题关键词热度 --")
for k, c in cnt.most_common(18):
    print(f"  {k}: {c}")

json.dump(out, open("/Users/liuguan1/Documents/github/Trading_assess/news_digest_raw.json", "w"),
          ensure_ascii=False, indent=2)
print("\n原始数据已存 news_digest_raw.json")
