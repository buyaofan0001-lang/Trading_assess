"""
筛选：2026 年初至今涨幅 >= 200% 的股票
剔除：科创板(688/689) / 创业板(300/301) / 北交所(.BJ) / 微盘股(总市值 < 50亿)
复权：用 adj_factor 计算真实区间收益(规避分红送股扭曲)
产出：movers_2026ytd.csv + movers_tv_symbols.json(TradingView 代码)
"""
import sys, time, json
sys.path.insert(0, '/Users/liuguan1/Documents/github/Trading_assess')
from tushare_client import get_pro
pro = get_pro()

def call(fn, **kw):
    last = None
    for _ in range(4):
        try:
            return fn(**kw)
        except Exception as e:
            last = e; time.sleep(3)
    raise last

# 1) 基准日(2026首个交易日) 与 最新交易日
cal = call(pro.trade_cal, exchange='SSE', start_date='20260101', end_date='20260125', is_open='1')
base_date = sorted(cal['cal_date'].tolist())[0]
cal2 = call(pro.trade_cal, exchange='SSE', start_date='20260601', end_date='20260620', is_open='1')
latest_date = sorted(cal2['cal_date'].tolist())[-1]
print(f"基准日={base_date}  最新日={latest_date}")

# 2) 基础信息 + 行情 + 复权因子 + 市值
sb = call(pro.stock_basic, exchange='', list_status='L', fields='ts_code,name,industry,market,list_date')
db = call(pro.daily, trade_date=base_date)[['ts_code', 'close']].rename(columns={'close': 'c_base'})
dl = call(pro.daily, trade_date=latest_date)[['ts_code', 'close']].rename(columns={'close': 'c_last'})
ab = call(pro.adj_factor, trade_date=base_date)[['ts_code', 'adj_factor']].rename(columns={'adj_factor': 'f_base'})
al = call(pro.adj_factor, trade_date=latest_date)[['ts_code', 'adj_factor']].rename(columns={'adj_factor': 'f_last'})
bsc = call(pro.daily_basic, trade_date=latest_date)[['ts_code', 'total_mv', 'turnover_rate']]
print(f"全市场: stock_basic={len(sb)}  base行情={len(db)}  latest行情={len(dl)}  市值={len(bsc)}")

m = (sb.merge(db, on='ts_code').merge(dl, on='ts_code')
       .merge(ab, on='ts_code').merge(al, on='ts_code').merge(bsc, on='ts_code', how='left'))
m['ret'] = (m['c_last'] * m['f_last']) / (m['c_base'] * m['f_base']) - 1   # 复权真实收益

n0 = len(m)
# 3) 剔除：仅保留主板，排除北交所
m = m[(m['market'] == '主板') & (~m['ts_code'].str.endswith('.BJ'))]
n1 = len(m)
# 剔除微盘：总市值 >= 50亿 (total_mv 单位万元)
CAP_FLOOR = 500000
m = m[m['total_mv'] >= CAP_FLOOR]
n2 = len(m)
# 涨幅 >= 200%
res = m[m['ret'] >= 2.0].copy().sort_values('ret', ascending=False)
print(f"\n漏斗: 全部{n0} → 主板非北交所{n1} → 市值≥50亿{n2} → 涨幅≥200% {len(res)}")

res['ret_pct'] = (res['ret'] * 100).round(0).astype(int)
res['mv_yi'] = (res['total_mv'] / 10000).round(0).astype(int)
res['is_st'] = res['name'].str.contains('ST')

print(f"\n=== 2026 年初至今涨幅≥200%(剔除创业板/科创板/北交所/微盘) 共 {len(res)} 只 ===")
for _, r in res.iterrows():
    st = ' [ST]' if r['is_st'] else ''
    print(f"  {r['ts_code']}  {r['name']:<8} +{r['ret_pct']}%  市值{r['mv_yi']}亿  {r['industry']}{st}")

def tv(code):
    c, ex = code.split('.')
    return ('SSE:' if ex == 'SH' else 'SZSE:') + c

res[['ts_code', 'name', 'ret_pct', 'mv_yi', 'industry', 'is_st']].to_csv(
    '/Users/liuguan1/Documents/github/Trading_assess/movers_2026ytd.csv', index=False)
syms = [tv(c) for c in res['ts_code']]
json.dump(syms, open('/Users/liuguan1/Documents/github/Trading_assess/movers_tv_symbols.json', 'w'))
print(f"\n已存 movers_2026ytd.csv 和 movers_tv_symbols.json ({len(syms)} 个 TV 代码)")
