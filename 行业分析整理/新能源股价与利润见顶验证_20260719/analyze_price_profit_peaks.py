#!/usr/bin/env python3
"""Compare the 2019-2022 new-energy equity-cycle price peaks with profit peaks.

Primary project market-data access is attempted through tushare_client.get_pro().
If the configured TuShare token is unavailable, the script falls back to AkShare's
Eastmoney-backed A-share price and financial statement endpoints and records the
actual source used in the output metadata.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import akshare as ak
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ARTIFACT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = ARTIFACT_DIR.parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tushare_client import get_pro


DATA_DIR = ARTIFACT_DIR / "data"
CHART_DIR = ARTIFACT_DIR / "charts"

PRICE_START = pd.Timestamp("2019-01-01")
PRICE_END = pd.Timestamp("2026-07-18")
CYCLE_END = pd.Timestamp("2022-12-31")
FIN_START = pd.Timestamp("2018-03-31")

COMPANIES = {
    "宁德时代": "300750.SZ",
    "隆基绿能": "601012.SH",
    "阳光电源": "300274.SZ",
    "亿纬锂能": "300014.SZ",
    "恩捷股份": "002812.SZ",
    "通威股份": "600438.SH",
    "天齐锂业": "002466.SZ",
    "锦浪科技": "300763.SZ",
}


def setup_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CHART_DIR.mkdir(parents=True, exist_ok=True)


def try_tushare_price(ts_code: str) -> tuple[pd.DataFrame | None, str | None]:
    """Return qfq daily closes from the project's canonical TuShare client."""
    try:
        pro = get_pro()
        daily = pro.daily(
            ts_code=ts_code,
            start_date=PRICE_START.strftime("%Y%m%d"),
            end_date=PRICE_END.strftime("%Y%m%d"),
            fields="ts_code,trade_date,open,high,low,close,vol,amount",
        )
        adj = pro.adj_factor(
            ts_code=ts_code,
            start_date=PRICE_START.strftime("%Y%m%d"),
            end_date=PRICE_END.strftime("%Y%m%d"),
            fields="ts_code,trade_date,adj_factor",
        )
        if daily.empty or adj.empty:
            return None, "TuShare returned no price or adjustment data"
        out = daily.merge(adj, on=["ts_code", "trade_date"], how="inner")
        out["date"] = pd.to_datetime(out["trade_date"])
        latest_factor = out.loc[out["date"].idxmax(), "adj_factor"]
        for column in ("open", "high", "low", "close"):
            out[column] = out[column] * out["adj_factor"] / latest_factor
        out = out[["date", "open", "high", "low", "close", "vol", "amount"]]
        return out.sort_values("date").reset_index(drop=True), None
    except Exception as exc:  # provider failures are preserved in metadata
        return None, f"{type(exc).__name__}: {exc}"


def fetch_price(ts_code: str) -> tuple[pd.DataFrame, dict[str, str | None]]:
    code = ts_code.split(".")[0]
    local_cache = DATA_DIR / f"{code}_history.csv"
    if local_cache.exists():
        cached = pd.read_csv(local_cache)
        cached["date"] = pd.to_datetime(cached["date"])
        cached = cached.rename(columns={"volume": "vol"})
        return cached[["date", "open", "high", "low", "close", "vol", "amount"]], {
            "provider": "AkShare stock_zh_a_hist / Eastmoney (local query cache)",
            "adjustment": "qfq",
            "fallback_reason": "TuShare token expired during the 2026-07-19 research run",
        }

    tushare_data, error = try_tushare_price(ts_code)
    if tushare_data is not None:
        return tushare_data, {
            "provider": "TuShare via project tushare_client.py",
            "adjustment": "qfq (computed from adj_factor)",
            "fallback_reason": None,
        }

    raw = ak.stock_zh_a_hist(
        symbol=code,
        period="daily",
        start_date=PRICE_START.strftime("%Y%m%d"),
        end_date=PRICE_END.strftime("%Y%m%d"),
        adjust="qfq",
    )
    if raw.empty:
        raise RuntimeError(f"No price data returned for {ts_code}")
    out = raw.rename(
        columns={
            "日期": "date",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "vol",
            "成交额": "amount",
        }
    )
    out["date"] = pd.to_datetime(out["date"])
    out = out[["date", "open", "high", "low", "close", "vol", "amount"]]
    return out.sort_values("date").reset_index(drop=True), {
        "provider": "AkShare stock_zh_a_hist / Eastmoney",
        "adjustment": "qfq",
        "fallback_reason": error,
    }


def fetch_quarterly_profit(ts_code: str) -> tuple[pd.DataFrame, dict[str, str]]:
    local_cache = DATA_DIR / f"{ts_code.replace('.', '_')}_quarterly_profit.csv"
    if local_cache.exists():
        cached = pd.read_csv(local_cache)
        for column in ("report_date", "notice_date", "update_date"):
            cached[column] = pd.to_datetime(cached[column], errors="coerce")
        return cached, {
            "provider": "AkShare stock_profit_sheet_by_quarterly_em / Eastmoney (local query cache)",
            "profit_basis": "single-quarter attributable net profit; TTM is rolling sum of four quarters",
        }

    market_prefix = "SH" if ts_code.endswith(".SH") else "SZ"
    code = ts_code.split(".")[0]
    raw = ak.stock_profit_sheet_by_quarterly_em(symbol=f"{market_prefix}{code}")
    keep = raw[
        [
            "REPORT_DATE",
            "REPORT_DATE_NAME",
            "NOTICE_DATE",
            "UPDATE_DATE",
            "PARENT_NETPROFIT",
            "DEDUCT_PARENT_NETPROFIT",
            "TOTAL_OPERATE_INCOME",
        ]
    ].copy()
    keep = keep.rename(
        columns={
            "REPORT_DATE": "report_date",
            "REPORT_DATE_NAME": "report_name",
            "NOTICE_DATE": "notice_date",
            "UPDATE_DATE": "update_date",
            "PARENT_NETPROFIT": "quarter_parent_profit",
            "DEDUCT_PARENT_NETPROFIT": "quarter_deduct_profit",
            "TOTAL_OPERATE_INCOME": "quarter_revenue",
        }
    )
    for column in ("report_date", "notice_date", "update_date"):
        keep[column] = pd.to_datetime(keep[column], errors="coerce")
    for column in ("quarter_parent_profit", "quarter_deduct_profit", "quarter_revenue"):
        keep[column] = pd.to_numeric(keep[column], errors="coerce")
    keep = (
        keep[keep["report_date"] >= FIN_START]
        .sort_values(["report_date", "update_date"])
        .drop_duplicates("report_date", keep="last")
        .reset_index(drop=True)
    )
    keep["ttm_parent_profit"] = keep["quarter_parent_profit"].rolling(4, min_periods=4).sum()
    keep["ttm_deduct_profit"] = keep["quarter_deduct_profit"].rolling(4, min_periods=4).sum()
    keep["ttm_revenue"] = keep["quarter_revenue"].rolling(4, min_periods=4).sum()
    return keep, {
        "provider": "AkShare stock_profit_sheet_by_quarterly_em / Eastmoney",
        "profit_basis": "single-quarter attributable net profit; TTM is rolling sum of four quarters",
    }


def months_between(start: pd.Timestamp, end: pd.Timestamp) -> int:
    return (end.year - start.year) * 12 + end.month - start.month


def derive_summary(name: str, code: str, price: pd.DataFrame, profit: pd.DataFrame) -> dict[str, object]:
    cycle = price[(price["date"] >= PRICE_START) & (price["date"] <= CYCLE_END)].copy()
    if cycle.empty:
        raise RuntimeError(f"No price data in cycle window for {name}")
    price_peak = cycle.loc[cycle["close"].idxmax()]
    full_price_peak = price.loc[price["close"].idxmax()]

    valid_ttm = profit.dropna(subset=["ttm_parent_profit"]).copy()
    profit_peak = valid_ttm.loc[valid_ttm["ttm_parent_profit"].idxmax()]
    latest = valid_ttm.iloc[-1]
    at_latest_high = profit_peak["report_date"] == latest["report_date"]
    profit_notice_date = (
        pd.Timestamp(profit_peak["notice_date"])
        if pd.notna(profit_peak["notice_date"])
        else pd.Timestamp(profit_peak["report_date"])
    )

    price_at_period_end = price[price["date"] <= profit_peak["report_date"]].iloc[-1]
    price_at_notice = price[price["date"] <= profit_notice_date].iloc[-1]

    valid_deduct = profit.dropna(subset=["ttm_deduct_profit"]).copy()
    deduct_peak = valid_deduct.loc[valid_deduct["ttm_deduct_profit"].idxmax()]

    annual = valid_ttm[valid_ttm["report_date"].dt.month.eq(12)].copy()
    annual_peak = annual.loc[annual["ttm_parent_profit"].idxmax()]
    annual_latest = annual.iloc[-1]
    annual_at_latest_high = annual_peak["report_date"] == annual_latest["report_date"]

    return {
        "company": name,
        "ts_code": code,
        "price_cycle_peak_date": price_peak["date"].date().isoformat(),
        "price_cycle_peak_close_qfq": round(float(price_peak["close"]), 4),
        "price_full_sample_peak_date": full_price_peak["date"].date().isoformat(),
        "price_full_sample_peak_close_qfq": round(float(full_price_peak["close"]), 4),
        "cycle_peak_remained_full_sample_high": bool(full_price_peak["date"] == price_peak["date"]),
        "max_observed_ttm_period": profit_peak["report_date"].date().isoformat(),
        "max_observed_ttm_notice_date": (
            profit_peak["notice_date"].date().isoformat()
            if pd.notna(profit_peak["notice_date"])
            else None
        ),
        "max_observed_ttm_parent_profit_100m_cny": round(float(profit_peak["ttm_parent_profit"]) / 1e8, 2),
        "ttm_lead_months_by_period_end": months_between(price_peak["date"], profit_peak["report_date"]),
        "ttm_lead_months_by_notice_date": months_between(price_peak["date"], profit_notice_date),
        "price_drawdown_at_profit_period_end_pct": round(
            (float(price_at_period_end["close"]) / float(price_peak["close"]) - 1) * 100, 2
        ),
        "price_drawdown_at_profit_notice_pct": round(
            (float(price_at_notice["close"]) / float(price_peak["close"]) - 1) * 100, 2
        ),
        "ttm_peak_is_latest_observation": bool(at_latest_high),
        "actual_ttm_profit_peak_observed": bool(not at_latest_high),
        "max_observed_ttm_deduct_period": deduct_peak["report_date"].date().isoformat(),
        "max_observed_ttm_deduct_profit_100m_cny": round(float(deduct_peak["ttm_deduct_profit"]) / 1e8, 2),
        "deduct_peak_after_cycle_price_peak": bool(deduct_peak["report_date"] > price_peak["date"]),
        "full_sample_price_peak_before_max_observed_ttm": bool(
            full_price_peak["date"] < profit_peak["report_date"]
        ),
        "annual_max_observed_year": int(annual_peak["report_date"].year),
        "annual_max_observed_parent_profit_100m_cny": round(float(annual_peak["ttm_parent_profit"]) / 1e8, 2),
        "annual_peak_is_latest_full_year": bool(annual_at_latest_high),
        "latest_ttm_period": latest["report_date"].date().isoformat(),
        "latest_ttm_parent_profit_100m_cny": round(float(latest["ttm_parent_profit"]) / 1e8, 2),
    }


def plot_comparison(all_prices: dict[str, pd.DataFrame], all_profits: dict[str, pd.DataFrame], summary: pd.DataFrame) -> None:
    plt.rcParams.update(
        {
            "font.family": ["Arial Unicode MS", "Hiragino Sans GB", "sans-serif"],
            "axes.unicode_minus": False,
            "font.size": 10,
        }
    )
    fig, axes = plt.subplots(4, 2, figsize=(18, 22), constrained_layout=True)
    fig.patch.set_facecolor("#f5f2ea")
    for ax, (name, code) in zip(axes.ravel(), COMPANIES.items()):
        price = all_prices[name].copy()
        profit = all_profits[name].dropna(subset=["ttm_parent_profit"]).copy()
        month = price.set_index("date")["close"].resample("ME").last().dropna()
        month = month / month.iloc[0] * 100
        row = summary.loc[summary["company"] == name].iloc[0]

        ax.set_facecolor("#fbfaf6")
        ax.plot(month.index, month.values, color="#c86538", lw=2.1, label="前复权股价指数（2019首月=100）")
        ax.axvspan(PRICE_START, CYCLE_END, color="#d6b26e", alpha=0.10)
        price_peak_date = pd.Timestamp(row["price_cycle_peak_date"])
        ax.axvline(price_peak_date, color="#a5482e", lw=1.2, ls="--")
        if not row["cycle_peak_remained_full_sample_high"]:
            ax.axvline(
                pd.Timestamp(row["price_full_sample_peak_date"]),
                color="#7a5b8a",
                lw=1.2,
                ls="-.",
                label="后续全样本新高",
            )
        ax.set_ylabel("股价指数", color="#a5482e")
        ax.tick_params(axis="y", labelcolor="#a5482e")
        ax.grid(axis="y", alpha=0.16)

        ax2 = ax.twinx()
        ax2.plot(
            profit["report_date"],
            profit["ttm_parent_profit"] / 1e8,
            color="#1e6a78",
            marker="o",
            ms=3.2,
            lw=2.0,
            label="TTM归母净利润",
        )
        profit_peak_date = pd.Timestamp(row["max_observed_ttm_period"])
        ax2.axvline(
            profit_peak_date,
            color="#1e6a78",
            lw=1.2,
            ls=":" if row["ttm_peak_is_latest_observation"] else "--",
        )
        ax2.set_ylabel("TTM归母净利润（亿元）", color="#1e6a78")
        ax2.tick_params(axis="y", labelcolor="#1e6a78")

        peak_label = "样本末最高（尚未确认见顶）" if row["ttm_peak_is_latest_observation"] else "已观察利润峰"
        new_high_label = (
            f" ｜ 后创新高 {row['price_full_sample_peak_date']}"
            if not row["cycle_peak_remained_full_sample_high"]
            else ""
        )
        ax.set_title(
            f"{name}  {code}\n股价周期峰 {row['price_cycle_peak_date']}{new_high_label}\n{peak_label} {row['max_observed_ttm_period']} ｜ 相差 {row['ttm_lead_months_by_period_end']}个月",
            loc="left",
            fontsize=12,
            fontweight="bold",
        )
        ax.xaxis.set_major_locator(mdates.YearLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
        ax.set_xlim(PRICE_START, PRICE_END)

        handles1, labels1 = ax.get_legend_handles_labels()
        handles2, labels2 = ax2.get_legend_handles_labels()
        ax.legend(handles1 + handles2, labels1 + labels2, loc="upper left", frameon=False, fontsize=8)

    fig.suptitle(
        "新能源代表股：2019–2022 股价周期峰 vs. 后续 TTM 归母净利润\n"
        "股价峰只在该轮牛市窗口内识别；利润延伸至最新可得报告期",
        fontsize=20,
        fontweight="bold",
        color="#243238",
    )
    fig.savefig(CHART_DIR / "price_vs_ttm_profit_8stocks.png", dpi=180, facecolor=fig.get_facecolor())
    plt.close(fig)


def plot_peak_lags(summary: pd.DataFrame) -> None:
    plt.rcParams.update(
        {
            "font.family": ["Arial Unicode MS", "Hiragino Sans GB", "sans-serif"],
            "axes.unicode_minus": False,
        }
    )
    ordered = summary.sort_values("ttm_lead_months_by_period_end")
    colors = np.where(ordered["ttm_peak_is_latest_observation"], "#8b9da6", "#1e6a78")
    fig, ax = plt.subplots(figsize=(12, 6.5), constrained_layout=True)
    fig.patch.set_facecolor("#f5f2ea")
    ax.set_facecolor("#fbfaf6")
    bars = ax.barh(ordered["company"], ordered["ttm_lead_months_by_period_end"], color=colors)
    for bar, (_, row) in zip(bars, ordered.iterrows()):
        suffix = "（右删失）" if row["ttm_peak_is_latest_observation"] else ""
        ax.text(
            bar.get_width() + 0.7,
            bar.get_y() + bar.get_height() / 2,
            f"{int(row['ttm_lead_months_by_period_end'])}个月{suffix}",
            va="center",
            fontsize=10,
        )
    ax.axvline(0, color="#444", lw=1)
    ax.set_xlabel("股价周期峰领先最大已观察TTM利润点（月）")
    ax.set_title("领先期比较：灰色为利润截至样本末仍创新高，不能称为已见顶", loc="left", fontsize=15, fontweight="bold")
    ax.grid(axis="x", alpha=0.18)
    fig.savefig(CHART_DIR / "peak_lead_months.png", dpi=180, facecolor=fig.get_facecolor())
    plt.close(fig)


def main() -> None:
    setup_dirs()
    prices: dict[str, pd.DataFrame] = {}
    profits: dict[str, pd.DataFrame] = {}
    metadata: dict[str, object] = {
        "generated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
        "price_window": [PRICE_START.date().isoformat(), PRICE_END.date().isoformat()],
        "cycle_peak_window": [PRICE_START.date().isoformat(), CYCLE_END.date().isoformat()],
        "profit_definition": "TTM attributable net profit = sum of the latest four reported single-quarter values",
        "companies": {},
    }
    rows: list[dict[str, object]] = []

    for name, code in COMPANIES.items():
        price, price_meta = fetch_price(code)
        profit, profit_meta = fetch_quarterly_profit(code)
        price.to_csv(DATA_DIR / f"{code.replace('.', '_')}_price_qfq.csv", index=False)
        profit.to_csv(DATA_DIR / f"{code.replace('.', '_')}_quarterly_profit.csv", index=False)
        prices[name] = price
        profits[name] = profit
        metadata["companies"][name] = {"ts_code": code, "price": price_meta, "financial": profit_meta}
        rows.append(derive_summary(name, code, price, profit))

    summary = pd.DataFrame(rows)
    summary.to_csv(DATA_DIR / "peak_summary.csv", index=False)
    (DATA_DIR / "source_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    plot_comparison(prices, profits, summary)
    plot_peak_lags(summary)
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
