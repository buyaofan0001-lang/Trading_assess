#!/usr/bin/env python3
"""Build daily industry rankings from AkShare + TuShare.

Pipeline:
1) Fetch industry board universe and constituents from AkShare.
2) Build market metrics (M) and valuation input (V) per stock.
3) Optionally enrich fundamentals (F/O) from TuShare.
4) Call calc_industry_scores.py engine to compute industry rankings.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import os
import re
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import akshare as ak

from calc_industry_scores import aggregate_to_industry, compute_scores, write_output


@dataclass
class StockMetric:
    date: str
    industry: str
    stock_code: str
    f_revenue_yoy: Optional[float] = None
    f_profit_yoy: Optional[float] = None
    f_ocf_yoy: Optional[float] = None
    o_contract_liab_yoy: Optional[float] = None
    o_capex_yoy: Optional[float] = None
    o_inventory_yoy: Optional[float] = None
    m_return_20d: Optional[float] = None
    m_is_up: Optional[float] = None
    m_net_inflow_ratio: Optional[float] = None
    v_pe_percentile: Optional[float] = None
    _pe_raw: Optional[float] = None

    def to_dict(self) -> Dict[str, Optional[float]]:
        return {
            "date": self.date,
            "industry": self.industry,
            "stock_code": self.stock_code,
            "f_revenue_yoy": self.f_revenue_yoy,
            "f_profit_yoy": self.f_profit_yoy,
            "f_ocf_yoy": self.f_ocf_yoy,
            "o_contract_liab_yoy": self.o_contract_liab_yoy,
            "o_capex_yoy": self.o_capex_yoy,
            "o_inventory_yoy": self.o_inventory_yoy,
            "m_return_20d": self.m_return_20d,
            "m_is_up": self.m_is_up,
            "m_net_inflow_ratio": self.m_net_inflow_ratio,
            "v_pe_percentile": self.v_pe_percentile,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Daily industry ranking from AkShare + TuShare")
    parser.add_argument("--date", default=dt.date.today().isoformat(), help="Ranking date, YYYY-MM-DD")
    parser.add_argument("--industries", default="", help="Comma-separated industry names; empty=all")
    parser.add_argument(
        "--output-dir",
        default=os.path.join(os.path.dirname(__file__), "daily_outputs"),
        help="Output directory for daily csv files",
    )
    parser.add_argument(
        "--tushare-token",
        default=os.getenv("TUSHARE_TOKEN", ""),
        help="TuShare token; fallback to env TUSHARE_TOKEN",
    )
    parser.add_argument(
        "--disable-tushare",
        action="store_true",
        help="Do not use TuShare even if token exists",
    )
    parser.add_argument(
        "--disable-20d-return",
        action="store_true",
        help="Skip per-stock 20D return fetch (faster, less accurate)",
    )
    parser.add_argument(
        "--max-stocks-per-industry",
        type=int,
        default=80,
        help="Cap stock count per industry to control runtime",
    )
    parser.add_argument("--pass-threshold", type=float, default=70.0, help="S1 pass threshold")
    parser.add_argument("--min-components", type=int, default=5, help="Minimum components in industry")
    parser.add_argument("--retry", type=int, default=2, help="Retries for data calls")
    parser.add_argument("--sleep", type=float, default=0.08, help="Sleep seconds between TuShare stock calls")
    return parser.parse_args()


def safe_float(v: object) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip()
    if s in {"", "None", "nan", "NaN", "--", "null"}:
        return None
    s = s.replace(",", "").replace("%", "")
    try:
        return float(s)
    except ValueError:
        return None


def pick_col(columns: Iterable[str], candidates: Sequence[str]) -> Optional[str]:
    existing = list(columns)
    for c in candidates:
        if c in existing:
            return c
    return None


def normalize_code(code: object) -> Optional[str]:
    if code is None:
        return None
    s = str(code).strip()
    if not s:
        return None
    digits = re.sub(r"\D", "", s)
    if len(digits) < 6:
        return None
    return digits[-6:]


def to_tushare_code(code6: str) -> str:
    if code6.startswith(("60", "68", "90")):
        return f"{code6}.SH"
    if code6.startswith(("00", "30")):
        return f"{code6}.SZ"
    if code6.startswith("8"):
        return f"{code6}.BJ"
    return f"{code6}.SZ"


def yoy(latest: Optional[float], prev: Optional[float]) -> Optional[float]:
    if latest is None or prev is None or prev == 0:
        return None
    return (latest - prev) / abs(prev) * 100.0


def previous_year_period(end_date: str) -> Optional[str]:
    s = str(end_date).strip()
    if len(s) != 8 or not s.isdigit():
        return None
    return f"{int(s[:4]) - 1}{s[4:]}"


def percentile_rank(values_sorted: Sequence[float], value: float) -> float:
    if not values_sorted:
        return 50.0
    pos = bisect.bisect_right(values_sorted, value)
    return pos / len(values_sorted) * 100.0


def safe_ak_call(fn_name: str, retry: int, **kwargs):
    fn = getattr(ak, fn_name)
    last_error: Optional[Exception] = None
    for _ in range(retry + 1):
        try:
            return fn(**kwargs)
        except Exception as exc:  # pragma: no cover - network/runtime dependent
            last_error = exc
            time.sleep(0.3)
    if last_error:
        raise last_error
    raise RuntimeError(f"akshare call failed: {fn_name}")


def load_industry_list(retry: int, selected: Sequence[str]) -> List[str]:
    df = safe_ak_call("stock_board_industry_name_em", retry=retry)
    name_col = pick_col(df.columns, ["板块名称", "名称", "行业名称"])
    if not name_col:
        raise ValueError(f"Cannot find industry name column in: {list(df.columns)}")
    names = [str(x).strip() for x in df[name_col].tolist() if str(x).strip()]
    if selected:
        selected_set = {x.strip() for x in selected if x.strip()}
        names = [x for x in names if x in selected_set]
    return names


def load_industry_flow_map(retry: int) -> Dict[str, float]:
    flow_df = safe_ak_call(
        "stock_sector_fund_flow_rank",
        retry=retry,
        indicator="今日",
        sector_type="行业资金流",
    )
    name_col = pick_col(flow_df.columns, ["名称", "行业", "板块名称"])
    ratio_col = pick_col(
        flow_df.columns,
        [
            "今日主力净流入-净占比",
            "今日主力净流入净占比",
            "主力净流入-净占比",
            "今日主力净占比",
            "净占比",
        ],
    )
    if not name_col or not ratio_col:
        return {}

    out: Dict[str, float] = {}
    for _, row in flow_df.iterrows():
        name = str(row[name_col]).strip()
        val = safe_float(row[ratio_col])
        if name and val is not None:
            out[name] = val
    return out


def calc_return_20d(code6: str, end_date: str, retry: int) -> Optional[float]:
    end = dt.date.fromisoformat(end_date)
    start = end - dt.timedelta(days=60)
    df = safe_ak_call(
        "stock_zh_a_hist",
        retry=retry,
        symbol=code6,
        period="daily",
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
        adjust="qfq",
    )
    close_col = pick_col(df.columns, ["收盘", "收盘价", "close"])
    if not close_col:
        return None
    closes = [safe_float(v) for v in df[close_col].tolist()]
    closes = [v for v in closes if v is not None]
    if len(closes) < 21:
        return None
    latest = closes[-1]
    prev = closes[-21]
    if prev == 0:
        return None
    return (latest / prev - 1.0) * 100.0


def fetch_akshare_stock_metrics(
    trade_date: str,
    industries: List[str],
    retry: int,
    disable_20d_return: bool,
    max_stocks_per_industry: int,
) -> List[StockMetric]:
    flow_map = load_industry_flow_map(retry=retry)
    out: List[StockMetric] = []

    for ind in industries:
        cons_df = safe_ak_call("stock_board_industry_cons_em", retry=retry, symbol=ind)
        code_col = pick_col(cons_df.columns, ["代码", "股票代码", "code"])
        pct_col = pick_col(cons_df.columns, ["涨跌幅", "涨跌幅(%)", "pct_chg"])
        pe_col = pick_col(cons_df.columns, ["市盈率-动态", "动态市盈率", "市盈率"])
        if not code_col:
            continue

        rows = cons_df.head(max_stocks_per_industry) if max_stocks_per_industry > 0 else cons_df
        flow_ratio = flow_map.get(ind)
        for _, row in rows.iterrows():
            code6 = normalize_code(row[code_col])
            if not code6:
                continue
            pct = safe_float(row[pct_col]) if pct_col else None
            pe = safe_float(row[pe_col]) if pe_col else None

            metric = StockMetric(
                date=trade_date,
                industry=ind,
                stock_code=code6,
                m_is_up=1.0 if pct is not None and pct > 0 else 0.0 if pct is not None else None,
                m_net_inflow_ratio=flow_ratio,
                _pe_raw=pe if pe is not None and pe > 0 else None,
            )

            if disable_20d_return:
                metric.m_return_20d = pct
            else:
                try:
                    metric.m_return_20d = calc_return_20d(code6=code6, end_date=trade_date, retry=retry)
                except Exception:
                    metric.m_return_20d = pct
            out.append(metric)
    return out


def enrich_tushare_fundamentals(
    metrics: List[StockMetric],
    token: str,
    retry: int,
    sleep_sec: float,
) -> None:
    import tushare as ts

    pro = ts.pro_api(token)
    unique_codes = sorted({m.stock_code for m in metrics})
    cache: Dict[str, Dict[str, Optional[float]]] = {}

    for code6 in unique_codes:
        ts_code = to_tushare_code(code6)
        cache[code6] = {
            "f_revenue_yoy": None,
            "f_profit_yoy": None,
            "f_ocf_yoy": None,
            "o_contract_liab_yoy": None,
            "o_capex_yoy": None,
            "o_inventory_yoy": None,
        }
        # latest fundamentals
        fi = _safe_tushare_query(
            pro=pro,
            retry=retry,
            api_name="fina_indicator",
            ts_code=ts_code,
            fields="ts_code,end_date,tr_yoy,netprofit_yoy,ocf_yoy",
            limit=1,
        )
        if fi is not None and not fi.empty:
            row = fi.iloc[0]
            cache[code6]["f_revenue_yoy"] = safe_float(row.get("tr_yoy"))
            cache[code6]["f_profit_yoy"] = safe_float(row.get("netprofit_yoy"))
            cache[code6]["f_ocf_yoy"] = safe_float(row.get("ocf_yoy"))

        # order/capex proxies from balance sheet + cashflow yoy
        bs = _safe_tushare_query(
            pro=pro,
            retry=retry,
            api_name="balancesheet",
            ts_code=ts_code,
            fields="ts_code,end_date,contract_liab,inventories",
            limit=1,
        )
        cf = _safe_tushare_query(
            pro=pro,
            retry=retry,
            api_name="cashflow",
            ts_code=ts_code,
            fields="ts_code,end_date,c_pay_acq_const_fiolta",
            limit=1,
        )
        latest_period = None
        if bs is not None and not bs.empty:
            latest_period = str(bs.iloc[0].get("end_date") or "")
        if (not latest_period) and cf is not None and not cf.empty:
            latest_period = str(cf.iloc[0].get("end_date") or "")
        prev_period = previous_year_period(latest_period) if latest_period else None

        if prev_period:
            bs_prev = _safe_tushare_query(
                pro=pro,
                retry=retry,
                api_name="balancesheet",
                ts_code=ts_code,
                period=prev_period,
                fields="ts_code,end_date,contract_liab,inventories",
                limit=1,
            )
            cf_prev = _safe_tushare_query(
                pro=pro,
                retry=retry,
                api_name="cashflow",
                ts_code=ts_code,
                period=prev_period,
                fields="ts_code,end_date,c_pay_acq_const_fiolta",
                limit=1,
            )
            if bs is not None and not bs.empty and bs_prev is not None and not bs_prev.empty:
                latest_contract = safe_float(bs.iloc[0].get("contract_liab"))
                prev_contract = safe_float(bs_prev.iloc[0].get("contract_liab"))
                latest_inv = safe_float(bs.iloc[0].get("inventories"))
                prev_inv = safe_float(bs_prev.iloc[0].get("inventories"))
                cache[code6]["o_contract_liab_yoy"] = yoy(latest_contract, prev_contract)
                cache[code6]["o_inventory_yoy"] = yoy(latest_inv, prev_inv)

            if cf is not None and not cf.empty and cf_prev is not None and not cf_prev.empty:
                latest_capex = safe_float(cf.iloc[0].get("c_pay_acq_const_fiolta"))
                prev_capex = safe_float(cf_prev.iloc[0].get("c_pay_acq_const_fiolta"))
                cache[code6]["o_capex_yoy"] = yoy(latest_capex, prev_capex)

        time.sleep(max(0.0, sleep_sec))

    for m in metrics:
        data = cache.get(m.stock_code)
        if not data:
            continue
        m.f_revenue_yoy = data["f_revenue_yoy"]
        m.f_profit_yoy = data["f_profit_yoy"]
        m.f_ocf_yoy = data["f_ocf_yoy"]
        m.o_contract_liab_yoy = data["o_contract_liab_yoy"]
        m.o_capex_yoy = data["o_capex_yoy"]
        m.o_inventory_yoy = data["o_inventory_yoy"]


def _safe_tushare_query(pro, retry: int, api_name: str, **kwargs):
    last_error: Optional[Exception] = None
    for _ in range(retry + 1):
        try:
            return pro.query(api_name, **kwargs)
        except Exception as exc:  # pragma: no cover - network/runtime dependent
            last_error = exc
            time.sleep(0.35)
    if last_error:
        return None
    return None


def assign_v_percentiles(metrics: List[StockMetric]) -> None:
    valid = sorted([m._pe_raw for m in metrics if m._pe_raw is not None and m._pe_raw > 0])
    for m in metrics:
        if m._pe_raw is None or m._pe_raw <= 0:
            m.v_pe_percentile = None
        else:
            m.v_pe_percentile = percentile_rank(valid, m._pe_raw)


def fill_missing_f_o_as_neutral(metrics: List[StockMetric]) -> None:
    # If TuShare is unavailable, keep F/O neutral by leaving them None.
    # Engine will map missing cross-sectional metrics to z=0.
    for m in metrics:
        if m.f_revenue_yoy is None:
            m.f_revenue_yoy = None
        if m.f_profit_yoy is None:
            m.f_profit_yoy = None
        if m.f_ocf_yoy is None:
            m.f_ocf_yoy = None
        if m.o_contract_liab_yoy is None:
            m.o_contract_liab_yoy = None
        if m.o_capex_yoy is None:
            m.o_capex_yoy = None
        if m.o_inventory_yoy is None:
            m.o_inventory_yoy = None


def write_stock_metrics(path: str, metrics: List[StockMetric]) -> None:
    fields = [
        "date",
        "industry",
        "stock_code",
        "f_revenue_yoy",
        "f_profit_yoy",
        "f_ocf_yoy",
        "o_contract_liab_yoy",
        "o_capex_yoy",
        "o_inventory_yoy",
        "m_return_20d",
        "m_is_up",
        "m_net_inflow_ratio",
        "v_pe_percentile",
    ]
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for m in metrics:
            d = m.to_dict()
            row = {}
            for k in fields:
                v = d.get(k)
                row[k] = "" if v is None else f"{float(v):.6f}" if isinstance(v, (int, float)) else v
            writer.writerow(row)


def main() -> None:
    args = parse_args()
    selected = [x.strip() for x in args.industries.split(",") if x.strip()]
    os.makedirs(args.output_dir, exist_ok=True)

    industries = load_industry_list(retry=args.retry, selected=selected)
    if not industries:
        raise ValueError("No industries found. Check --industries filter.")

    metrics = fetch_akshare_stock_metrics(
        trade_date=args.date,
        industries=industries,
        retry=args.retry,
        disable_20d_return=args.disable_20d_return,
        max_stocks_per_industry=args.max_stocks_per_industry,
    )
    if not metrics:
        raise ValueError("No stock metrics fetched from AkShare.")

    use_tushare = (not args.disable_tushare) and bool(args.tushare_token.strip())
    if use_tushare:
        enrich_tushare_fundamentals(
            metrics=metrics,
            token=args.tushare_token.strip(),
            retry=args.retry,
            sleep_sec=args.sleep,
        )
    else:
        fill_missing_f_o_as_neutral(metrics)

    assign_v_percentiles(metrics)

    stock_out = os.path.join(args.output_dir, f"stock_metrics_{args.date}.csv")
    write_stock_metrics(stock_out, metrics)

    stock_rows = [m.to_dict() for m in metrics]
    industry_rows = aggregate_to_industry(stock_rows)
    industry_rows = compute_scores(
        industry_rows=industry_rows,
        pass_threshold=args.pass_threshold,
        min_components=args.min_components,
    )

    score_out = os.path.join(args.output_dir, f"industry_scores_{args.date}.csv")
    write_output(score_out, industry_rows)

    sorted_rows = sorted(industry_rows, key=lambda x: x.score, reverse=True)
    print(f"date={args.date} industries={len(industries)} stocks={len(metrics)}")
    print(f"tushare_used={int(use_tushare)}")
    print(f"stock_metrics={stock_out}")
    print(f"industry_scores={score_out}")
    print("top10:")
    for row in sorted_rows[:10]:
        print(f"  {row.rank:>2}. {row.industry:<16} {row.score:6.2f}  pass={row.pass_to_s2}")


if __name__ == "__main__":
    main()
