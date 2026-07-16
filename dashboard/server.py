#!/usr/bin/env python3
"""Trading Control dashboard server.

Serves a zero-build frontend and a JSON API backed by the repository's
canonical Tushare client plus yfinance for the last completed US session.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math
import statistics
import sys
import threading
import time
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import pandas as pd
import akshare as ak
import yfinance as yf

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))

from tushare_client import get_pro  # noqa: E402

CONFIG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
PERIOD_DAYS = {"1d": 1, "5d": 5, "20d": 20}
INTRADAY_TTL_SECONDS = 25


class TTLCache:
    def __init__(self) -> None:
        self._items: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self._inflight: dict[str, threading.Event] = {}

    def get(self, key: str, ttl: int, loader: Callable[[], Any]) -> Any:
        while True:
            now = time.time()
            with self._lock:
                cached = self._items.get(key)
                if cached and now - cached[0] < ttl:
                    return cached[1]
                waiter = self._inflight.get(key)
                if waiter is None:
                    waiter = threading.Event()
                    self._inflight[key] = waiter
                    break
            waiter.wait(timeout=120)

        try:
            value = loader()
        except Exception:
            with self._lock:
                self._inflight.pop(key, None)
                waiter.set()
            raise
        with self._lock:
            self._items[key] = (time.time(), value)
            self._inflight.pop(key, None)
            waiter.set()
        return value

    def invalidate_prefix(self, prefix: str) -> None:
        with self._lock:
            for key in [key for key in self._items if key.startswith(prefix)]:
                self._items.pop(key, None)


CACHE = TTLCache()
TUSHARE_SLOTS = threading.BoundedSemaphore(3)


def tushare_call(method: Callable[..., Any], **kwargs: Any) -> Any:
    """Respect the self-hosted endpoint's small concurrent-connection limit."""
    with TUSHARE_SLOTS:
        return method(**kwargs)


def finite(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def a_quote(ts_code: str) -> dict[str, Any]:
    """Get one current A-share snapshot; batch permission is not assumed."""

    def load() -> dict[str, Any]:
        if ts_code.startswith(("5", "1")):
            daily = fund_history(ts_code)
            row = daily.iloc[-1].to_dict()
            close = finite(row.get("close"))
            pre_close = finite(row.get("pre_close"))
            amount = finite(row.get("amount"))
            volume = finite(row.get("vol"))
            # fund_daily: amount is thousand yuan; vol is hands (100 shares).
            vwap = amount * 10 / volume if amount is not None and volume else None
            return {
                "ts_code": ts_code,
                "name": ts_code,
                "price": close,
                "pre_close": pre_close,
                "return": (close / pre_close - 1) if close is not None and pre_close else None,
                "open": finite(row.get("open")),
                "high": finite(row.get("high")),
                "low": finite(row.get("low")),
                "volume": volume,
                "amount": amount,
                "vwap": vwap,
                "vs_vwap": (close / vwap - 1) if close is not None and vwap else None,
                "market_date": str(row.get("trade_date") or ""),
                "source": f"Tushare fund_daily · 最近收盘 {row.get('trade_date', '')}",
            }
        df = tushare_call(get_pro().rt_k, ts_code=ts_code)
        if df is None or df.empty:
            raise RuntimeError(f"{ts_code} 无实时快照")
        row = df.iloc[0].to_dict()
        close = finite(row.get("close"))
        pre_close = finite(row.get("pre_close"))
        amount = finite(row.get("amount"))
        volume = finite(row.get("vol"))
        # rt_k: amount is yuan; vol is hands (100 shares).
        vwap = amount / (volume * 100) if amount is not None and volume else None
        return {
            "ts_code": ts_code,
            "name": str(row.get("name") or ts_code),
            "price": close,
            "pre_close": pre_close,
            "return": (close / pre_close - 1) if close is not None and pre_close else None,
            "open": finite(row.get("open")),
            "high": finite(row.get("high")),
            "low": finite(row.get("low")),
            "volume": volume,
            "amount": amount,
            "vwap": vwap,
            "vs_vwap": (close / vwap - 1) if close is not None and vwap else None,
            "source": "Tushare rt_k · 单代码轮询",
        }

    return CACHE.get(f"rt:{ts_code}", 25, load)


def fund_history(ts_code: str) -> pd.DataFrame:
    def load() -> pd.DataFrame:
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=80)).strftime("%Y%m%d")
        df = tushare_call(get_pro().fund_daily, ts_code=ts_code, start_date=start, end_date=end)
        if df is None or df.empty:
            raise RuntimeError(f"{ts_code} 无基金日线历史")
        return df.sort_values("trade_date").reset_index(drop=True)

    return CACHE.get(f"fund_daily:{ts_code}", 1800, load)


def history(ts_code: str) -> pd.DataFrame:
    if ts_code.startswith(("5", "1")):
        return fund_history(ts_code)

    def load() -> pd.DataFrame:
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=80)).strftime("%Y%m%d")
        df = tushare_call(get_pro().daily, ts_code=ts_code, start_date=start, end_date=end)
        if df is None or df.empty:
            raise RuntimeError(f"{ts_code} 无日线历史")
        return df.sort_values("trade_date").reset_index(drop=True)

    return CACHE.get(f"daily:{ts_code}", 1800, load)


def period_return(ts_code: str, days: int, live: dict[str, Any] | None) -> float | None:
    if days == 1 and live:
        return finite(live.get("return"))
    df = history(ts_code)
    closes = [finite(v) for v in df["close"].tolist()]
    closes = [v for v in closes if v is not None]
    if not closes:
        return None
    latest = finite(live.get("price")) if live else None
    if latest is None:
        latest = closes[-1]
        baseline_index = -(days + 1)
    else:
        # If today's live price is already the last daily bar, avoid double counting.
        baseline_index = -(days + 1) if abs(latest - closes[-1]) < 1e-9 else -days
    if len(closes) < abs(baseline_index):
        return None
    baseline = closes[baseline_index]
    return latest / baseline - 1 if baseline else None


def turnover_intensity(ts_code: str, amount: float | None) -> float | None:
    """Current traded amount as a percentage of the latest circulating market value."""
    if amount is None or ts_code.startswith(("5", "1")):
        return None

    def load() -> float | None:
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=15)).strftime("%Y%m%d")
        df = tushare_call(
            get_pro().daily_basic,
            ts_code=ts_code,
            start_date=start,
            end_date=end,
            fields="ts_code,trade_date,circ_mv",
        )
        if df is None or df.empty:
            return None
        circ_mv = finite(df.sort_values("trade_date").iloc[-1].get("circ_mv"))
        # rt_k amount is yuan; daily_basic circ_mv is thousand yuan.
        return circ_mv

    circ_mv = CACHE.get(f"circ_mv:{ts_code}", 1800, load)
    return amount / (circ_mv * 1000) if circ_mv else None


def member_snapshot(item: dict[str, Any], timeframe: str, errors: list[str]) -> dict[str, Any]:
    ts_code = item["ts_code"]
    live: dict[str, Any] | None = None
    try:
        live = a_quote(ts_code)
    except Exception as exc:  # data gaps should not blank the entire board
        errors.append(f"{ts_code} 实时行情：{exc}")
    try:
        ret = period_return(ts_code, PERIOD_DAYS[timeframe], live)
    except Exception as exc:
        errors.append(f"{ts_code} {timeframe}收益：{exc}")
        ret = None
    try:
        intensity = turnover_intensity(ts_code, finite(live.get("amount")) if live else None)
    except Exception as exc:
        errors.append(f"{ts_code} 换手强度：{exc}")
        intensity = None
    return {
        "ts_code": ts_code,
        "name": item.get("name") or (live or {}).get("name") or ts_code,
        "price": finite((live or {}).get("price")),
        "pre_close": finite((live or {}).get("pre_close")),
        "return": ret,
        "vs_vwap": finite((live or {}).get("vs_vwap")),
        "turnover_intensity": intensity,
        "source": (live or {}).get("source", "Tushare 日线"),
    }


def score_group(
    holding: dict[str, Any],
    holding_row: dict[str, Any],
    peer_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    valid_peers = [row["return"] for row in peer_rows if row["return"] is not None]
    peer_median = statistics.median(valid_peers) if valid_peers else None
    holding_return = holding_row["return"]
    excess = holding_return - peer_median if holding_return is not None and peer_median is not None else None
    valid_all = [row for row in [holding_row, *peer_rows] if row["return"] is not None]
    ordered = sorted(valid_all, key=lambda row: row["return"], reverse=True)
    rank = next((index + 1 for index, row in enumerate(ordered) if row["ts_code"] == holding["ts_code"]), None)
    percentile = (1 - (rank - 1) / max(len(ordered) - 1, 1)) if rank is not None else None

    if excess is None or percentile is None:
        strength = "数据不足"
    elif excess > 0 and percentile >= 0.70:
        strength = "强"
    elif excess < 0 and percentile <= 0.30:
        strength = "弱"
    else:
        strength = "中性"

    if peer_median is None or holding_return is None:
        matrix = "等待完整行情"
    elif peer_median > 0 and holding_return > peer_median:
        matrix = "同行走强 · 持仓领涨"
    elif peer_median > 0 and holding_return <= peer_median:
        matrix = "同行走强 · 持仓落后"
    elif peer_median <= 0 and holding_return > peer_median:
        matrix = "同行承压 · 持仓抗跌"
    else:
        matrix = "同行承压 · 持仓更弱"

    avg_cost = finite(holding.get("avg_cost"))
    shares = finite(holding.get("shares"))
    current_price = finite(holding_row.get("price"))
    pnl_pct = current_price / avg_cost - 1 if current_price is not None and avg_cost else None
    pnl_amount = (current_price - avg_cost) * shares if current_price is not None and avg_cost and shares else None
    return {
        "holding": {
            **holding_row,
            "shares": holding.get("shares"),
            "shares_display": holding["shares_display"],
            "avg_cost": avg_cost,
            "total_cost": finite(holding.get("total_cost")),
            "pnl_pct": pnl_pct,
            "pnl_amount": pnl_amount,
            "confidence": holding.get("confidence"),
        },
        "peers": peer_rows,
        "cohort_type": holding["cohort_type"],
        "benchmark": holding["benchmark"],
        "cohort_status": "候选名单 · 需用户确认后锁定",
        "peer_median": peer_median,
        "excess": excess,
        "rank": rank,
        "member_count": len(valid_all),
        "percentile": percentile,
        "strength": strength,
        "matrix": matrix,
    }


def build_groups(timeframe: str, errors: list[str]) -> list[dict[str, Any]]:
    """Fetch every unique cohort member concurrently, then score groups locally."""
    items: dict[str, dict[str, Any]] = {}
    for holding in CONFIG["holdings"]:
        items[holding["ts_code"]] = holding
        for peer in holding["peers"]:
            items[peer["ts_code"]] = peer

    snapshots: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(6, len(items)), thread_name_prefix="quotes") as executor:
        futures = {
            executor.submit(member_snapshot, item, timeframe, errors): ts_code
            for ts_code, item in items.items()
        }
        for future in as_completed(futures):
            ts_code = futures[future]
            try:
                snapshots[ts_code] = future.result()
            except Exception as exc:
                errors.append(f"{ts_code} 同行快照：{exc}")
                snapshots[ts_code] = {
                    "ts_code": ts_code,
                    "name": items[ts_code].get("name") or ts_code,
                    "price": None,
                    "pre_close": None,
                    "return": None,
                    "vs_vwap": None,
                    "turnover_intensity": None,
                    "source": "数据暂不可用",
                }

    return [
        score_group(
            holding,
            snapshots[holding["ts_code"]],
            [snapshots[peer["ts_code"]] for peer in holding["peers"]],
        )
        for holding in CONFIG["holdings"]
    ]


def yahoo_intraday_fallback(items: list[dict[str, Any]], trade_date: str) -> dict[str, list[dict[str, Any]]]:
    """Fetch all cohort members in one request so upstream fallback stays cheap."""

    tickers = {
        item["ts_code"]: f"{item['ts_code'].split('.')[0]}.{'SS' if item['ts_code'].endswith('.SH') else 'SZ'}"
        for item in items
    }

    def load() -> dict[str, list[dict[str, Any]]]:
        frame = yf.download(
            tickers=list(tickers.values()),
            period="1d",
            interval="1m",
            auto_adjust=False,
            progress=False,
            group_by="ticker",
            threads=True,
            timeout=15,
        )
        result: dict[str, list[dict[str, Any]]] = {}
        for ts_code, ticker in tickers.items():
            try:
                data = frame[ticker] if len(tickers) > 1 else frame
                closes = data["Close"].dropna()
                points = []
                for stamp, value in closes.items():
                    if getattr(stamp, "tzinfo", None) is not None:
                        stamp = stamp.tz_convert("Asia/Shanghai")
                    if stamp.strftime("%Y-%m-%d") != trade_date:
                        continue
                    minute = stamp.hour * 60 + stamp.minute
                    if not (570 <= minute <= 690 or 780 <= minute <= 900):
                        continue
                    price = finite(value)
                    if price is not None:
                        points.append({"time": stamp.strftime("%H:%M"), "price": price})
                if points:
                    result[ts_code] = points
            except Exception:
                continue
        return result

    compact_date = trade_date.replace("-", "")
    return CACHE.get(f"intraday:yahoo:{compact_date}", INTRADAY_TTL_SECONDS, load)


def intraday_series(
    item: dict[str, Any],
    trade_date: str,
    fallback_points: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Fetch today's one-minute bars and normalize them against the previous close."""

    ts_code = item["ts_code"]

    def load() -> dict[str, Any]:
        quote = a_quote(ts_code)
        pre_close = finite(quote.get("pre_close"))
        if pre_close is None or pre_close <= 0:
            raise RuntimeError("缺少昨收，无法计算可比涨跌幅")

        raw_points: list[dict[str, Any]] = []
        source = "AkShare / 东方财富 · 1分钟不复权"
        primary_error: Exception | None = None
        try:
            start = f"{trade_date} 09:30:00"
            end = f"{trade_date} 15:00:00"
            endpoint = ak.fund_etf_hist_min_em if ts_code.startswith(("5", "1")) else ak.stock_zh_a_hist_min_em
            frame = endpoint(
                symbol=ts_code.split(".")[0],
                period="1",
                start_date=start,
                end_date=end,
                adjust="",
            )
            if frame is None or frame.empty:
                raise RuntimeError(f"{trade_date} 无分钟行情")
            required = {"时间", "收盘"}
            if not required.issubset(frame.columns):
                raise RuntimeError(f"分钟行情字段缺失：{sorted(required - set(frame.columns))}")
            for _, row in frame.iterrows():
                price = finite(row.get("收盘"))
                stamp = pd.to_datetime(row.get("时间"), errors="coerce")
                if price is not None and not pd.isna(stamp):
                    raw_points.append({"time": stamp.strftime("%H:%M"), "price": price})
        except Exception as exc:
            primary_error = exc

        if not raw_points and fallback_points:
            raw_points = fallback_points
            source = "Yahoo Finance · 1分钟回退"
        if not raw_points:
            detail = f"；主源错误：{primary_error}" if primary_error else ""
            raise RuntimeError(f"{trade_date} 无分钟行情{detail}")

        points = [
            {
                "time": point["time"],
                "price": point["price"],
                "return": point["price"] / pre_close - 1,
            }
            for point in raw_points
        ]
        if not points:
            raise RuntimeError("分钟行情没有有效价格点")
        return {
            "ts_code": ts_code,
            "name": item.get("name") or quote.get("name") or ts_code,
            "pre_close": pre_close,
            "points": points,
            "latest_time": points[-1]["time"],
            "latest_return": points[-1]["return"],
            "source": source,
        }

    compact_date = trade_date.replace("-", "")
    return CACHE.get(f"intraday:{ts_code}:{compact_date}", INTRADAY_TTL_SECONDS, load)


def build_intraday_payload() -> dict[str, Any]:
    started = time.perf_counter()
    trade_date = datetime.now().strftime("%Y-%m-%d")
    items: dict[str, dict[str, Any]] = {}
    for holding in CONFIG["holdings"]:
        items[holding["ts_code"]] = holding
        for peer in holding["peers"]:
            items[peer["ts_code"]] = peer

    series: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    try:
        fallback = yahoo_intraday_fallback(list(items.values()), trade_date)
    except Exception as exc:
        fallback = {}
        errors.append(f"Yahoo批量回退：{exc}")
    # Eastmoney's minute endpoint is sensitive to bursts, so keep primary calls serial.
    for ts_code, item in items.items():
        try:
            series[ts_code] = intraday_series(item, trade_date, fallback.get(ts_code))
        except Exception as exc:
            errors.append(f"{ts_code}：{exc}")

    groups = []
    for holding in CONFIG["holdings"]:
        member_codes = [holding["ts_code"], *[peer["ts_code"] for peer in holding["peers"]]]
        groups.append({
            "holding_ts_code": holding["ts_code"],
            "series": [series[code] for code in member_codes if code in series],
            "missing": [code for code in member_codes if code not in series],
        })
    return {
        "meta": {
            "generated_at": iso_now(),
            "trade_date": trade_date,
            "source": "东方财富1分钟优先 · Yahoo Finance分钟线回退",
            "partial": bool(errors),
            "errors": errors,
            "fallback_count": sum(row.get("source", "").startswith("Yahoo") for row in series.values()),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        },
        "groups": groups,
    }


def us_market(errors: list[str]) -> dict[str, Any]:
    tickers = list(dict.fromkeys(
        [row["ticker"] for row in CONFIG["us_market"]]
        + [row["ticker"] for holding in CONFIG["holdings"] for row in holding.get("us_map", [])]
    ))

    def load() -> dict[str, Any]:
        frame = yf.download(
            tickers=tickers,
            period="10d",
            interval="1d",
            auto_adjust=False,
            progress=False,
            group_by="ticker",
            threads=True,
        )
        rows: dict[str, Any] = {}
        for ticker in tickers:
            try:
                data = frame[ticker] if len(tickers) > 1 else frame
                closes = data["Close"].dropna()
                if len(closes) < 2:
                    raise RuntimeError("不足两个收盘价")
                latest = finite(closes.iloc[-1])
                previous = finite(closes.iloc[-2])
                idx = closes.index[-1]
                rows[ticker] = {
                    "ticker": ticker,
                    "date": idx.strftime("%Y-%m-%d"),
                    "close": latest,
                    "return": latest / previous - 1 if latest is not None and previous else None,
                }
            except Exception as exc:
                errors.append(f"美股 {ticker}：{exc}")
        return rows

    try:
        raw = CACHE.get("us:last_session", 1800, load)
    except Exception as exc:
        errors.append(f"美股批量行情：{exc}")
        raw = {}

    market_rows = [{**item, **raw.get(item["ticker"], {})} for item in CONFIG["us_market"]]
    mapped = []
    for holding in CONFIG["holdings"]:
        peers = [{**item, **raw.get(item["ticker"], {})} for item in holding.get("us_map", [])]
        if peers:
            mapped.append({"holding": holding["name"], "peers": peers, "status": "候选映射 · 待确认"})
    dates = [row.get("date") for row in market_rows if row.get("date")]
    market_date = max(dates) if dates else None
    return {
        "market_date": market_date,
        "beijing_mapping": f"北京时间 {datetime.now().strftime('%Y-%m-%d')} 盘前参考" if market_date else "日期待确认",
        "source": "Yahoo Finance · 最近完整交易日",
        "market": market_rows,
        "mapped_peers": mapped,
        "rule": "隔夜美股只作环境映射，不计入A股当日同行排名",
    }


def money_flow(errors: list[str]) -> dict[str, Any]:
    """Fetch the latest available post-close THS industry/concept flows."""

    def load() -> dict[str, Any]:
        pro = get_pro()
        for offset in range(0, 8):
            trade_date = (datetime.now() - timedelta(days=offset)).strftime("%Y%m%d")
            industries = tushare_call(pro.moneyflow_ind_ths, trade_date=trade_date)
            concepts = tushare_call(pro.moneyflow_cnt_ths, trade_date=trade_date)
            if industries is not None and not industries.empty:
                def shape(df: pd.DataFrame, limit: int = 6) -> list[dict[str, Any]]:
                    amount_col = next((c for c in ["net_amount", "net_amount_rate", "pct_change"] if c in df.columns), None)
                    name_col = next((c for c in ["name", "industry", "concept"] if c in df.columns), None)
                    if not amount_col or not name_col:
                        return []
                    clean = df.copy()
                    clean[amount_col] = pd.to_numeric(clean[amount_col], errors="coerce")
                    clean = clean.dropna(subset=[amount_col]).sort_values(amount_col, ascending=False)
                    return [
                        {"name": str(row[name_col]), "value": finite(row[amount_col]), "metric": amount_col}
                        for _, row in clean.head(limit).iterrows()
                    ]

                return {
                    "trade_date": trade_date,
                    "industries": shape(industries),
                    "concepts": shape(concepts) if concepts is not None else [],
                }
        raise RuntimeError("最近8天无可用盘后资金流")

    try:
        result = CACHE.get("money_flow:latest", 1800, load)
        return {**result, "source": "Tushare 同花顺口径 · 盘后更新", "realtime": False}
    except Exception as exc:
        errors.append(f"板块资金流：{exc}")
        return {"trade_date": None, "industries": [], "concepts": [], "source": "盘后数据暂不可用", "realtime": False}


def permission_card(errors: list[str], groups: list[dict[str, Any]]) -> dict[str, Any]:
    longdian = next((group["holding"] for group in groups if group["holding"]["ts_code"] == "600584.SH"), None)
    loss = finite((longdian or {}).get("pnl_pct"))
    hard_stop = next(
        (finite(item.get("hard_stop_pct")) for item in CONFIG["holdings"] if item["ts_code"] == "600584.SH"),
        None,
    )
    stop_breached = loss is not None and hard_stop is not None and loss <= -hard_stop
    level = "red"
    if stop_breached:
        reason = (
            f"长电科技900股已确认，当前相对99.854元成本约{loss * 100:.1f}%，"
            f"已低于-{hard_stop * 100:.0f}%规则线；恢复期规则同时生效。"
        )
    else:
        reason = "长电科技900股已确认；恢复期前5个交易日仍在执行。"
    return {
        "level": level,
        "label": "停止主动买入",
        "reason": reason,
        "allowed": ["核对券商持仓", "执行既定止损/减仓", "收盘后复盘"],
        "blocked": ["新增标的", "补亏损仓", "盘中临时改条件"],
        "next_decision": "14:30–14:55，仅处理事先写好的计划",
        "data_warning": f"另有 {len(errors)} 项数据缺口" if errors else None,
    }


def build_dashboard(timeframe: str) -> dict[str, Any]:
    if timeframe not in PERIOD_DAYS:
        timeframe = "1d"
    started = time.perf_counter()
    errors: list[str] = []
    get_pro()  # initialize the shared client before worker threads use it
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="sections") as executor:
        groups_future = executor.submit(build_groups, timeframe, errors)
        us_future = executor.submit(us_market, errors)
        flows_future = executor.submit(money_flow, errors)
        groups = groups_future.result()
        us = us_future.result()
        flows = flows_future.result()
    position_items = [
        {
            "name": group["holding"]["name"],
            "ts_code": group["holding"]["ts_code"],
            "shares_display": group["holding"]["shares_display"],
            "avg_cost": group["holding"]["avg_cost"],
            "price": group["holding"]["price"],
            "pnl_pct": group["holding"]["pnl_pct"],
            "pnl_amount": group["holding"]["pnl_amount"],
            "source": group["holding"]["confidence"],
        }
        for group in groups
    ]
    return {
        "meta": {
            "generated_at": iso_now(),
            "timeframe": timeframe,
            "partial": bool(errors),
            "errors": errors,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "truth_note": "价格按单代码轮询；不同股票快照可能相差数秒。资金流为盘后口径。",
        },
        "permission": permission_card(errors, groups),
        "holdings_status": {
            "confirmed": "长电科技900股 · 科创50ETF 100份",
            "unresolved": None,
            "portfolio_pnl_enabled": True,
            "positions": position_items,
        },
        "peer_groups": groups,
        "us": us,
        "money_flow": flows,
        "recovery": {
            "title": "恢复期 · 第1阶段",
            "rule": "不靠下一笔交易解决生活压力",
            "checklist": CONFIG["recovery_checklist"],
        },
    }


def build_peer_payload(timeframe: str) -> dict[str, Any]:
    if timeframe not in PERIOD_DAYS:
        timeframe = "1d"
    started = time.perf_counter()
    errors: list[str] = []
    get_pro()
    groups = build_groups(timeframe, errors)
    return {
        "meta": {
            "generated_at": iso_now(),
            "timeframe": timeframe,
            "partial": bool(errors),
            "errors": errors,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        },
        "peer_groups": groups,
    }


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(HERE), **kwargs)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in {"/api/dashboard", "/api/peer-groups", "/api/intraday"}:
            timeframe = parse_qs(parsed.query).get("timeframe", ["1d"])[0]
            force = parse_qs(parsed.query).get("force", ["0"])[0] == "1"
            try:
                if force:
                    CACHE.invalidate_prefix("rt:")
                    CACHE.invalidate_prefix("intraday:")
                if parsed.path == "/api/dashboard":
                    payload = build_dashboard(timeframe)
                elif parsed.path == "/api/peer-groups":
                    payload = build_peer_payload(timeframe)
                else:
                    payload = build_intraday_payload()
                body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
                self.send_response(HTTPStatus.OK)
            except Exception as exc:
                body = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
                self.send_response(HTTPStatus.INTERNAL_SERVER_ERROR)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        if parsed.path == "/":
            self.path = "/index.html"
        super().do_GET()

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {fmt % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Trading Control dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"交易看板已启动：http://{args.host}:{args.port}")
    print("按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
