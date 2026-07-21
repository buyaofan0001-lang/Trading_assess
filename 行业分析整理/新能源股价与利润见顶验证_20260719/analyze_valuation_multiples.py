#!/usr/bin/env python3
"""Build point-in-time TTM/static PE histories and a clearly labelled ex-post NTM PE.

Historical consensus forward-PE series are not exposed by the public provider. The
script therefore uses original research reports only as sparse near-cycle-peak FY1
forecast anchors. The continuous "ex-post NTM PE" uses subsequently realised four-
quarter profit and must not be interpreted as the market's historical expectation.
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
START = pd.Timestamp("2019-01-01")
END = pd.Timestamp("2026-07-17")

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

# Original research-report anchors closest to the cycle price peaks. Values are
# the report's next-fiscal-year forecast PE, not a reconstructed daily consensus.
FORWARD_ANCHORS = {
    "宁德时代": {
        "price_basis_date": "2021-11-01",
        "report_date": "2021-11-01",
        "forecast_year": 2022,
        "fy1_pe_reported": 91.06,
        "institution": "国元证券",
        "url": "https://pdf.dfcfw.com/pdf/H3_AP202111011526431124_1.pdf",
    },
    "隆基绿能": {
        "price_basis_date": "2021-10-29",
        "report_date": "2021-11-01",
        "forecast_year": 2022,
        "fy1_pe_reported": 35.68,
        "institution": "山西证券",
        "url": "https://pdf.dfcfw.com/pdf/H3_AP202111011526378839_1.pdf",
    },
    "阳光电源": {
        "price_basis_date": "2021-09-01",
        "report_date": "2021-09-03",
        "forecast_year": 2022,
        "fy1_pe_reported": 60.25,
        "institution": "山西证券",
        "url": "https://pdf.dfcfw.com/pdf/H3_AP202109031513988826_1.pdf",
    },
    "亿纬锂能": {
        "price_basis_date": "2021-11-08",
        "report_date": "2021-11-08",
        "forecast_year": 2022,
        "fy1_pe_reported": 43.21,
        "institution": "东吴证券",
        "url": "https://pdf.dfcfw.com/pdf/H3_AP202111081527752050_1.pdf",
    },
    "恩捷股份": {
        "price_basis_date": "2021-09-06",
        "report_date": "2021-09-06",
        "forecast_year": 2022,
        "fy1_pe_reported": 49.00,
        "institution": "华金证券",
        "url": "https://pdf.dfcfw.com/pdf/H3_AP202109061514610321_1.pdf",
    },
    "通威股份": {
        "price_basis_date": "2022-07-05",
        "report_date": "2022-07-05",
        "forecast_year": 2023,
        "fy1_pe_reported": 15.28,
        "institution": "西南证券",
        "url": "https://pdf.dfcfw.com/pdf/H3_AP202207051575819324_1.pdf",
    },
    "天齐锂业": {
        "price_basis_date": "2022-06-29",
        "report_date": "2022-06-30",
        "forecast_year": 2023,
        "fy1_pe_reported": 11.40,
        "institution": "东亚前海证券",
        "url": "https://pdf.dfcfw.com/pdf/H3_AP202206301575605564_1.pdf",
    },
    "锦浪科技": {
        "price_basis_date": "2022-07-15",
        "report_date": "2022-07-18",
        "forecast_year": 2023,
        "fy1_pe_reported": 31.80,
        "institution": "东吴证券",
        "url": "https://pdf.dfcfw.com/pdf/H3_AP202207171576261392_1.pdf",
    },
}


def probe_tushare() -> str | None:
    try:
        pro = get_pro()
        pro.daily_basic(ts_code="300750.SZ", start_date="20210104", end_date="20210104", fields="ts_code,trade_date,pe,pe_ttm,total_mv")
        return None
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


def fetch_valuation(name: str, code: str) -> pd.DataFrame:
    cache = DATA_DIR / f"{code.replace('.', '_')}_valuation_daily.csv"
    if cache.exists():
        frame = pd.read_csv(cache)
        frame["date"] = pd.to_datetime(frame["date"])
        return frame

    raw = ak.stock_value_em(symbol=code.split(".")[0])
    frame = raw.rename(
        columns={
            "数据日期": "date",
            "当日收盘价": "close_raw",
            "总市值": "total_mv",
            "总股本": "total_shares",
            "PE(TTM)": "pe_ttm",
            "PE(静)": "pe_static",
        }
    )
    frame["date"] = pd.to_datetime(frame["date"])
    numeric = ["close_raw", "total_mv", "total_shares", "pe_ttm", "pe_static"]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame[(frame["date"] >= START) & (frame["date"] <= END)].copy()
    frame = frame[["date", *numeric]].sort_values("date").reset_index(drop=True)
    frame.to_csv(cache, index=False)
    return frame


def add_ex_post_ntm(frame: pd.DataFrame, code: str) -> pd.DataFrame:
    profit_path = DATA_DIR / f"{code.replace('.', '_')}_quarterly_profit.csv"
    profit = pd.read_csv(profit_path)
    profit["report_date"] = pd.to_datetime(profit["report_date"])
    profit = profit.sort_values("report_date").dropna(subset=["quarter_parent_profit"])
    q_dates = profit["report_date"].to_numpy(dtype="datetime64[ns]")
    q_profit = profit["quarter_parent_profit"].to_numpy(dtype=float)

    values: list[float] = []
    for date, mv in zip(frame["date"], frame["total_mv"]):
        idx = int(np.searchsorted(q_dates, np.datetime64(date), side="right"))
        if idx + 4 <= len(q_profit):
            denominator = float(q_profit[idx : idx + 4].sum())
            values.append(float(mv) / denominator if denominator != 0 else np.nan)
        else:
            values.append(np.nan)
    out = frame.copy()
    out["pe_ex_post_ntm"] = values
    return out


def nearest_on_or_before(frame: pd.DataFrame, target: pd.Timestamp) -> pd.Series:
    eligible = frame[frame["date"] <= target]
    if eligible.empty:
        raise ValueError(f"No valuation row on or before {target.date()}")
    return eligible.iloc[-1]


def build_peak_summary(name: str, code: str, frame: pd.DataFrame, peak_row: pd.Series) -> dict[str, object]:
    peak_date = pd.Timestamp(peak_row["price_cycle_peak_date"])
    valuation_at_peak = nearest_on_or_before(frame, peak_date)
    anchor = FORWARD_ANCHORS[name]
    anchor_date = pd.Timestamp(anchor["price_basis_date"])
    valuation_at_anchor = nearest_on_or_before(frame, anchor_date)
    proxy_fy1_pe_at_peak = (
        float(anchor["fy1_pe_reported"])
        * float(valuation_at_peak["close_raw"])
        / float(valuation_at_anchor["close_raw"])
    )
    return {
        "company": name,
        "ts_code": code,
        "cycle_price_peak_date": peak_date.date().isoformat(),
        "pe_ttm_at_cycle_peak": round(float(valuation_at_peak["pe_ttm"]), 2),
        "pe_static_at_cycle_peak": round(float(valuation_at_peak["pe_static"]), 2),
        "pe_ex_post_ntm_at_cycle_peak": round(float(valuation_at_peak["pe_ex_post_ntm"]), 2),
        "forecast_anchor_report_date": anchor["report_date"],
        "forecast_anchor_price_basis_date": anchor["price_basis_date"],
        "forecast_anchor_institution": anchor["institution"],
        "forecast_year": anchor["forecast_year"],
        "fy1_pe_reported": anchor["fy1_pe_reported"],
        "fy1_pe_scaled_to_cycle_peak": round(proxy_fy1_pe_at_peak, 2),
        "forecast_anchor_gap_days": int((peak_date - pd.Timestamp(anchor["report_date"])).days),
        "forecast_anchor_url": anchor["url"],
    }


def build_key_date_rows(name: str, code: str, frame: pd.DataFrame, peak_row: pd.Series) -> list[dict[str, object]]:
    """Sample the valuation series at comparable milestones.

    The profit-peak observation uses the announcement date, because that is when
    investors could first know the corresponding reported result.
    """
    milestones = [
        ("2019起点", frame.iloc[0]),
        ("旧周期股价峰值", nearest_on_or_before(frame, pd.Timestamp(peak_row["price_cycle_peak_date"]))),
        (
            "已观察TTM利润峰值披露日",
            nearest_on_or_before(frame, pd.Timestamp(peak_row["max_observed_ttm_notice_date"])),
        ),
        ("最新", frame.iloc[-1]),
    ]
    rows: list[dict[str, object]] = []
    for milestone, row in milestones:
        rows.append(
            {
                "company": name,
                "ts_code": code,
                "milestone": milestone,
                "valuation_date": pd.Timestamp(row["date"]).date().isoformat(),
                "pe_ttm": round(float(row["pe_ttm"]), 2) if pd.notna(row["pe_ttm"]) else np.nan,
                "pe_static": round(float(row["pe_static"]), 2) if pd.notna(row["pe_static"]) else np.nan,
                "pe_ex_post_ntm": round(float(row["pe_ex_post_ntm"]), 2)
                if pd.notna(row["pe_ex_post_ntm"])
                else np.nan,
            }
        )
    return rows


def positive(series: pd.Series) -> pd.Series:
    return series.where(series > 0)


def add_loss_spans(ax: plt.Axes, monthly: pd.DataFrame) -> None:
    loss = monthly["pe_ttm"].le(0).fillna(False)
    start: pd.Timestamp | None = None
    for date, is_loss in loss.items():
        if is_loss and start is None:
            start = pd.Timestamp(date)
        if not is_loss and start is not None:
            ax.axvspan(start, pd.Timestamp(date), color="#b94a48", alpha=0.08)
            start = None
    if start is not None:
        ax.axvspan(start, monthly.index[-1], color="#b94a48", alpha=0.08)


def plot_histories(frames: dict[str, pd.DataFrame], peak_summary: pd.DataFrame, profit_peaks: pd.DataFrame) -> None:
    plt.rcParams.update(
        {
            "font.family": ["Arial Unicode MS", "Hiragino Sans GB", "sans-serif"],
            "axes.unicode_minus": False,
            "font.size": 10,
        }
    )
    fig, axes = plt.subplots(4, 2, figsize=(18, 23), constrained_layout=True)
    fig.patch.set_facecolor("#f5f2ea")
    for ax, (name, code) in zip(axes.ravel(), COMPANIES.items()):
        frame = frames[name].set_index("date")
        monthly = frame.resample("ME").last()
        peak = peak_summary[peak_summary["company"] == name].iloc[0]
        profit_peak = profit_peaks[profit_peaks["company"] == name].iloc[0]

        ax.set_facecolor("#fbfaf6")
        add_loss_spans(ax, monthly)
        ax.plot(monthly.index, positive(monthly["pe_ttm"]), color="#1e6a78", lw=2.0, label="PE(TTM)，当时已披露")
        ax.plot(monthly.index, positive(monthly["pe_static"]), color="#c86538", lw=1.8, label="静态PE，上一完整年度")
        ax.plot(
            monthly.index,
            positive(monthly["pe_ex_post_ntm"]),
            color="#765184",
            lw=1.7,
            ls="--",
            label="事后实现NTM PE（非当时预期）",
        )
        ax.axvline(pd.Timestamp(peak["cycle_price_peak_date"]), color="#a5482e", ls="--", lw=1.2, label="旧周期股价峰")
        ax.axvline(pd.Timestamp(profit_peak["max_observed_ttm_period"]), color="#1e6a78", ls=":", lw=1.2, label="最大已观察TTM利润期")
        ax.scatter(
            pd.Timestamp(peak["cycle_price_peak_date"]),
            peak["fy1_pe_scaled_to_cycle_peak"],
            marker="D",
            s=40,
            color="#222",
            zorder=6,
            label="峰值附近研报FY1 PE锚点",
        )
        ax.set_yscale("log")
        ax.set_ylim(2, 500)
        ax.set_xlim(START, END)
        ax.grid(which="both", axis="y", alpha=0.17)
        ax.xaxis.set_major_locator(mdates.YearLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
        ax.set_ylabel("市盈率（倍，对数轴；亏损期不计算PE）")
        ax.set_title(
            f"{name}  {code}\n周期峰：TTM {peak['pe_ttm_at_cycle_peak']:.1f}x ｜ 静态 {peak['pe_static_at_cycle_peak']:.1f}x ｜ 研报FY1约 {peak['fy1_pe_scaled_to_cycle_peak']:.1f}x ｜ 事后NTM {peak['pe_ex_post_ntm_at_cycle_peak']:.1f}x",
            loc="left",
            fontsize=12,
            fontweight="bold",
        )
        ax.legend(loc="upper right", fontsize=7.4, frameon=False, ncol=2)

    fig.suptitle(
        "新能源代表股：TTM PE、静态 PE 与远期估值锚\n"
        "黑色菱形来自股价峰附近原始研报；紫色连续线是事后实现利润口径，不等于当时一致预期",
        fontsize=19,
        fontweight="bold",
        color="#243238",
    )
    fig.savefig(CHART_DIR / "pe_history_8stocks.png", dpi=180, facecolor=fig.get_facecolor())
    plt.close(fig)


def plot_peak_comparison(summary: pd.DataFrame) -> None:
    plt.rcParams.update(
        {
            "font.family": ["Arial Unicode MS", "Hiragino Sans GB", "sans-serif"],
            "axes.unicode_minus": False,
        }
    )
    metrics = [
        ("pe_ttm_at_cycle_peak", "TTM PE", "#1e6a78"),
        ("pe_static_at_cycle_peak", "静态 PE", "#c86538"),
        ("fy1_pe_scaled_to_cycle_peak", "峰值附近研报FY1 PE", "#222222"),
        ("pe_ex_post_ntm_at_cycle_peak", "事后实现NTM PE", "#765184"),
    ]
    x = np.arange(len(summary))
    width = 0.19
    fig, ax = plt.subplots(figsize=(15, 7), constrained_layout=True)
    fig.patch.set_facecolor("#f5f2ea")
    ax.set_facecolor("#fbfaf6")
    for i, (column, label, color) in enumerate(metrics):
        bars = ax.bar(x + (i - 1.5) * width, summary[column], width, label=label, color=color, alpha=0.9)
        for bar in bars:
            value = bar.get_height()
            ax.text(bar.get_x() + bar.get_width() / 2, value + 1.2, f"{value:.0f}", ha="center", va="bottom", fontsize=8, rotation=90)
    ax.set_xticks(x, summary["company"])
    ax.set_ylabel("市盈率（倍）")
    ax.set_title("旧新能源周期股价峰附近的四种估值口径", loc="left", fontsize=16, fontweight="bold")
    ax.grid(axis="y", alpha=0.16)
    ax.legend(frameon=False, ncol=4, loc="upper right")
    fig.savefig(CHART_DIR / "pe_at_cycle_peak_comparison.png", dpi=180, facecolor=fig.get_facecolor())
    plt.close(fig)


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CHART_DIR.mkdir(parents=True, exist_ok=True)
    tushare_error = probe_tushare()
    profit_peaks = pd.read_csv(DATA_DIR / "peak_summary.csv")

    frames: dict[str, pd.DataFrame] = {}
    rows: list[dict[str, object]] = []
    key_date_rows: list[dict[str, object]] = []
    for name, code in COMPANIES.items():
        frame = add_ex_post_ntm(fetch_valuation(name, code), code)
        frame.to_csv(DATA_DIR / f"{code.replace('.', '_')}_valuation_with_expost_ntm.csv", index=False)
        frames[name] = frame
        peak_row = profit_peaks[profit_peaks["company"] == name].iloc[0]
        rows.append(build_peak_summary(name, code, frame, peak_row))
        key_date_rows.extend(build_key_date_rows(name, code, frame, peak_row))

    summary = pd.DataFrame(rows)
    summary.to_csv(DATA_DIR / "valuation_at_cycle_peak_summary.csv", index=False)
    pd.DataFrame(key_date_rows).to_csv(DATA_DIR / "valuation_key_dates.csv", index=False)
    (DATA_DIR / "forward_pe_anchor_sources.json").write_text(
        json.dumps(
            {
                "generated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
                "tushare_probe_error": tushare_error,
                "ttm_static_provider": "AkShare stock_value_em / Eastmoney",
                "continuous_forward_note": "pe_ex_post_ntm uses future realised four-quarter profit; it is hindsight, not historical consensus",
                "anchors": FORWARD_ANCHORS,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    plot_histories(frames, summary, profit_peaks)
    plot_peak_comparison(summary)
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
