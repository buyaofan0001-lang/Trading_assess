from tushare_client import get_pro
pro = get_pro()
codes = {'600183':'生益','002384':'东山','000636':'风华','002463':'沪电','002916':'深南'}
for code,name in codes.items():
    suf = code+'.SH' if code.startswith('6') else code+'.SZ'
    try:
        df = pro.daily(ts_code=suf, start_date='20260610').sort_values('trade_date')
        out=[f"{r['trade_date'][4:]}:{r['close']}({r['pct_chg']:+.1f}%)" for _,r in df.tail(7).iterrows()]
        print(name, suf, ' '.join(out))
    except Exception as e:
        print(name,'ERR',str(e)[:80])
print('---INDEX---')
for idx,nm in [('000001.SH','上证'),('399006.SZ','创业板'),('399303.SZ','国证2000')]:
    try:
        df = pro.index_daily(ts_code=idx, start_date='20260616').sort_values('trade_date')
        out=[f"{r['trade_date'][4:]}:{r['close']:.0f}({r['pct_chg']:+.1f}%)" for _,r in df.tail(6).iterrows()]
        print(nm, ' '.join(out))
    except Exception as e:
        print(nm,'ERR',str(e)[:80])
# 风华高科 YTD 与 60日位置
try:
    df = pro.daily(ts_code='000636.SZ', start_date='20251231').sort_values('trade_date')
    base = df.iloc[0]['close']; last = df.iloc[-1]['close']; hi = df['close'].max()
    print(f"风华YTD: 年初{base} 最新{last} 涨{(last/base-1)*100:.0f}% 期内最高{hi} 现价距高{(last/hi-1)*100:.0f}%")
except Exception as e:
    print('风华YTD ERR', str(e)[:80])
