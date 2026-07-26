#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

# statsmodels 0.14 expects the pre-pandas-3 two-argument decorator signature.
import pandas.util._decorators as pd_decorators

_pandas_deprecate_kwarg = pd_decorators.deprecate_kwarg


def _compat_deprecate_kwarg(*args, **kwargs):
    if len(args) >= 3 and isinstance(args[0], type) and issubclass(args[0], Warning):
        return _pandas_deprecate_kwarg(*args, **kwargs)
    return _pandas_deprecate_kwarg(FutureWarning, *args, **kwargs)


pd_decorators.deprecate_kwarg = _compat_deprecate_kwarg

from statsmodels.regression.linear_model import OLS  # noqa: E402
from statsmodels.stats.multitest import multipletests
from statsmodels.tools.tools import add_constant  # noqa: E402
from statsmodels.tsa.stattools import adfuller, coint


ROOT = Path("/Users/liuguan1/Documents/github/Trading_assess")
OUT = ROOT / "outputs" / "baml_csi300_20260726"
TMP = Path("/tmp/codex-baml-csi300-20260726")
START = pd.Timestamp("2016-07-26")
REQUEST_END = pd.Timestamp("2026-07-26")

FRED_URL = (
    "https://fred.stlouisfed.org/graph/fredgraph.csv"
    "?id=BAMLH0A0HYM2&cosd=2016-07-26&coed=2026-07-26"
)
HF_URL = (
    "https://huggingface.co/datasets/Sashank-810/crisisnet-dataset/"
    "resolve/main/Module_1/credit_spreads/BAMLH0A0HYM2.csv?download=true"
)
ARCHIVE_CROSSCHECK_URL = (
    "https://raw.githubusercontent.com/maaurocp/Trading_Protocol/"
    "bf64e83fa4c2a6e72c37d3883476dc81bd9d2e31/"
    "data/raw/fred_BAMLH0A0HYM2.csv"
)
HF_PUBLISHED_SHA256 = "416bf42bfb469635224e75544251b6d19f9de93550085645cd5900a6eede675c"


def download(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as response:
        path.write_bytes(response.read())


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_oas(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, na_values=[".", ""])
    date_col = next(c for c in df.columns if c.lower() in {"date", "observation_date", "unnamed: 0"})
    value_col = "BAMLH0A0HYM2"
    out = df[[date_col, value_col]].rename(columns={date_col: "date", value_col: "oas_percent"})
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["oas_percent"] = pd.to_numeric(out["oas_percent"], errors="coerce")
    return out.dropna().drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)


def pearson_spearman(x: pd.Series, y: pd.Series) -> dict[str, float | int]:
    z = pd.concat([x, y], axis=1).dropna()
    pr = stats.pearsonr(z.iloc[:, 0], z.iloc[:, 1])
    sr = stats.spearmanr(z.iloc[:, 0], z.iloc[:, 1])
    return {
        "n": int(len(z)),
        "pearson_r": float(pr.statistic),
        "pearson_p": float(pr.pvalue),
        "spearman_rho": float(sr.statistic),
        "spearman_p": float(sr.pvalue),
    }


def nw_regression(y: pd.Series, x: pd.Series, maxlags: int = 5) -> dict[str, float | int]:
    z = pd.concat([y.rename("y"), x.rename("x")], axis=1).dropna()
    model = OLS(z["y"], add_constant(z["x"])).fit(
        cov_type="HAC", cov_kwds={"maxlags": maxlags}
    )
    return {
        "n": int(model.nobs),
        "beta": float(model.params["x"]),
        "beta_hac_se": float(model.bse["x"]),
        "beta_hac_t": float(model.tvalues["x"]),
        "beta_hac_p": float(model.pvalues["x"]),
        "r_squared": float(model.rsquared),
        "maxlags": maxlags,
    }


def adf_result(series: pd.Series, label: str) -> dict[str, float | int | str]:
    values = series.dropna().astype(float)
    stat, pvalue, usedlag, nobs, *_ = adfuller(values, autolag="AIC")
    return {
        "series": label,
        "n": int(nobs),
        "adf_stat": float(stat),
        "p_value": float(pvalue),
        "used_lag": int(usedlag),
        "stationary_5pct": bool(pvalue < 0.05),
    }


def block_bootstrap_corr(
    x: pd.Series,
    y: pd.Series,
    block: int = 20,
    iterations: int = 3000,
    seed: int = 20260726,
) -> dict[str, float | int]:
    z = pd.concat([x, y], axis=1).dropna().to_numpy()
    n = len(z)
    rng = np.random.default_rng(seed)
    corrs = np.empty(iterations)
    blocks_needed = math.ceil(n / block)
    for i in range(iterations):
        starts = rng.integers(0, n, size=blocks_needed)
        idx = np.concatenate([(np.arange(s, s + block) % n) for s in starts])[:n]
        sample = z[idx]
        corrs[i] = np.corrcoef(sample[:, 0], sample[:, 1])[0, 1]
    return {
        "iterations": iterations,
        "block_length": block,
        "ci_low_95": float(np.quantile(corrs, 0.025)),
        "ci_high_95": float(np.quantile(corrs, 0.975)),
    }


def fmt_p(value: float) -> str:
    return "<0.0001" if value < 0.0001 else f"={value:.4f}"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)

    fred_path = TMP / "BAMLH0A0HYM2_fred.csv"
    hf_path = TMP / "BAMLH0A0HYM2_hf.csv"
    archive_path = TMP / "BAMLH0A0HYM2_trading_protocol.csv"
    download(FRED_URL, fred_path)
    download(HF_URL, hf_path)
    download(ARCHIVE_CROSSCHECK_URL, archive_path)

    fred = read_oas(fred_path)
    hf = read_oas(hf_path)
    archive = read_oas(archive_path)
    hf_sha = sha256(hf_path)
    if hf_sha != HF_PUBLISHED_SHA256:
        raise RuntimeError(f"Hugging Face archive SHA mismatch: {hf_sha}")

    overlap_hf_fred = hf.merge(fred, on="date", suffixes=("_hf", "_fred"))
    overlap_hf_archive = hf.merge(archive, on="date", suffixes=("_hf", "_archive"))
    hf_fred_max_diff = float(
        (overlap_hf_fred["oas_percent_hf"] - overlap_hf_fred["oas_percent_fred"]).abs().max()
    )
    hf_archive_max_diff = float(
        (overlap_hf_archive["oas_percent_hf"] - overlap_hf_archive["oas_percent_archive"]).abs().max()
    )
    if hf_fred_max_diff > 1e-12 or hf_archive_max_diff > 1e-12:
        raise RuntimeError("Archived BAMLH0A0HYM2 values do not reconcile exactly with overlaps.")

    # Current FRED observations take precedence; the verified archive fills the licensed history gap.
    cutoff = fred["date"].min()
    oas = pd.concat(
        [
            hf.loc[hf["date"] < cutoff].assign(source="Archived FRED mirror (Hugging Face)"),
            fred.assign(source="FRED current rolling window"),
        ],
        ignore_index=True,
    )
    oas = (
        oas.loc[(oas["date"] >= START) & (oas["date"] <= REQUEST_END)]
        .drop_duplicates("date", keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )
    oas["oas_bps"] = oas["oas_percent"] * 100.0
    oas["oas_change_bps"] = oas["oas_bps"].diff()

    sys.path.insert(0, str(ROOT))
    from tushare_client import get_pro  # noqa: PLC0415

    pro = get_pro()
    csi = pro.index_daily(
        ts_code="000300.SH",
        start_date=START.strftime("%Y%m%d"),
        end_date=REQUEST_END.strftime("%Y%m%d"),
        fields="ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount",
    )
    csi = csi.rename(columns={"trade_date": "date"}).copy()
    csi["date"] = pd.to_datetime(csi["date"])
    csi = csi.sort_values("date").reset_index(drop=True)
    csi["log_return"] = np.log(csi["close"] / csi["pre_close"])
    csi["source"] = "TuShare index_daily"

    aligned = csi.merge(
        oas[["date", "oas_percent", "oas_bps", "oas_change_bps", "source"]],
        on="date",
        how="inner",
        suffixes=("_csi300", "_oas"),
    )
    aligned = aligned.rename(
        columns={
            "source_csi300": "csi300_source",
            "source_oas": "oas_source",
            "pct_chg": "csi300_pct_chg",
            "log_return": "csi300_log_return",
        }
    )

    # Investable timing: for each China session, only use the latest US close strictly before that date.
    predictive = pd.merge_asof(
        csi.sort_values("date"),
        oas[["date", "oas_percent", "oas_bps", "oas_change_bps"]]
        .rename(columns={"date": "oas_date"})
        .sort_values("oas_date"),
        left_on="date",
        right_on="oas_date",
        direction="backward",
        allow_exact_matches=False,
    )
    predictive = predictive.rename(
        columns={"pct_chg": "csi300_pct_chg", "log_return": "csi300_log_return"}
    )
    predictive["oas_age_calendar_days"] = (predictive["date"] - predictive["oas_date"]).dt.days

    level_stats = pearson_spearman(aligned["oas_bps"], aligned["close"])
    change_stats = pearson_spearman(aligned["oas_change_bps"], aligned["csi300_log_return"])
    predictive_stats = pearson_spearman(
        predictive["oas_change_bps"], predictive["csi300_log_return"]
    )
    nw_change = nw_regression(aligned["csi300_log_return"], aligned["oas_change_bps"])
    nw_predictive = nw_regression(
        predictive["csi300_log_return"], predictive["oas_change_bps"]
    )
    boot_change = block_bootstrap_corr(
        aligned["oas_change_bps"], aligned["csi300_log_return"]
    )
    boot_predictive = block_bootstrap_corr(
        predictive["oas_change_bps"], predictive["csi300_log_return"]
    )

    adf = [
        adf_result(aligned["oas_bps"], "BAMLH0A0HYM2 level (bps)"),
        adf_result(aligned["close"], "CSI300 close"),
        adf_result(aligned["oas_change_bps"], "BAMLH0A0HYM2 daily change (bps)"),
        adf_result(aligned["csi300_log_return"], "CSI300 log return"),
    ]
    coint_stat, coint_p, _ = coint(
        aligned["close"].astype(float), aligned["oas_bps"].astype(float), autolag="aic"
    )

    lead_lag_rows: list[dict[str, float | int | str]] = []
    for k in range(-5, 6):
        # k > 0 means OAS change at t against CSI300 return k common sessions later.
        y = aligned["csi300_log_return"].shift(-k)
        stats_k = pearson_spearman(aligned["oas_change_bps"], y)
        lead_lag_rows.append(
            {
                "k_common_sessions": k,
                "definition": (
                    "ΔOAS(t) vs CSI return(t+k); k>0 tests future CSI return"
                ),
                **stats_k,
            }
        )
    lead_lag = pd.DataFrame(lead_lag_rows)
    lead_lag["pearson_q_bh"] = multipletests(
        lead_lag["pearson_p"], method="fdr_bh"
    )[1]

    rolling = aligned[["date", "oas_change_bps", "csi300_log_return"]].copy()
    rolling["rolling_63d_corr"] = rolling["oas_change_bps"].rolling(63).corr(
        rolling["csi300_log_return"]
    )
    rolling["rolling_252d_corr"] = rolling["oas_change_bps"].rolling(252).corr(
        rolling["csi300_log_return"]
    )

    annual_rows = []
    for year, group in aligned.groupby(aligned["date"].dt.year):
        if len(group) < 30:
            continue
        s = pearson_spearman(group["oas_change_bps"], group["csi300_log_return"])
        annual_rows.append({"year": int(year), **s})
    annual = pd.DataFrame(annual_rows)

    periods = [
        ("2016-07-26–2019-12-31", "2016-07-26", "2019-12-31"),
        ("2020-01-01–2022-12-31", "2020-01-01", "2022-12-31"),
        ("2023-01-01–2026-07-23", "2023-01-01", "2026-07-23"),
    ]
    subperiod_rows = []
    for label, start, end in periods:
        g = aligned.loc[aligned["date"].between(start, end)]
        s = pearson_spearman(g["oas_change_bps"], g["csi300_log_return"])
        subperiod_rows.append({"period": label, **s})
    subperiod = pd.DataFrame(subperiod_rows)

    results = {
        "requested_period": {
            "start": START.strftime("%Y-%m-%d"),
            "end": REQUEST_END.strftime("%Y-%m-%d"),
        },
        "actual_ranges": {
            "oas_start": oas["date"].min().strftime("%Y-%m-%d"),
            "oas_end": oas["date"].max().strftime("%Y-%m-%d"),
            "csi300_start": csi["date"].min().strftime("%Y-%m-%d"),
            "csi300_end": csi["date"].max().strftime("%Y-%m-%d"),
            "common_start": aligned["date"].min().strftime("%Y-%m-%d"),
            "common_end": aligned["date"].max().strftime("%Y-%m-%d"),
        },
        "row_counts": {
            "oas": int(len(oas)),
            "csi300": int(len(csi)),
            "same_date_common": int(len(aligned)),
            "predictive_prior_us": int(
                predictive[["oas_change_bps", "csi300_log_return"]].dropna().shape[0]
            ),
        },
        "archive_validation": {
            "hf_sha256": hf_sha,
            "hf_sha_matches_published": hf_sha == HF_PUBLISHED_SHA256,
            "hf_fred_overlap_rows": int(len(overlap_hf_fred)),
            "hf_fred_max_abs_diff_pct_points": hf_fred_max_diff,
            "hf_archive_overlap_rows": int(len(overlap_hf_archive)),
            "hf_archive_max_abs_diff_pct_points": hf_archive_max_diff,
            "fred_current_first_date": cutoff.strftime("%Y-%m-%d"),
        },
        "level_same_date": level_stats,
        "daily_change_same_date": {
            **change_stats,
            "newey_west": nw_change,
            "block_bootstrap": boot_change,
        },
        "prior_us_close_predictive": {
            **predictive_stats,
            "newey_west": nw_predictive,
            "block_bootstrap": boot_predictive,
        },
        "stationarity_adf": adf,
        "cointegration": {
            "engle_granger_stat": float(coint_stat),
            "p_value": float(coint_p),
            "cointegrated_5pct": bool(coint_p < 0.05),
        },
        "rolling_252": {
            "latest": float(rolling["rolling_252d_corr"].dropna().iloc[-1]),
            "median": float(rolling["rolling_252d_corr"].dropna().median()),
            "min": float(rolling["rolling_252d_corr"].dropna().min()),
            "max": float(rolling["rolling_252d_corr"].dropna().max()),
        },
    }

    oas_out = oas[["date", "oas_percent", "oas_bps", "oas_change_bps", "source"]].copy()
    csi_out = csi[
        [
            "date",
            "ts_code",
            "open",
            "high",
            "low",
            "close",
            "pre_close",
            "change",
            "pct_chg",
            "log_return",
            "vol",
            "amount",
            "source",
        ]
    ].copy()
    aligned_out = aligned[
        [
            "date",
            "close",
            "pre_close",
            "csi300_pct_chg",
            "csi300_log_return",
            "oas_percent",
            "oas_bps",
            "oas_change_bps",
            "csi300_source",
            "oas_source",
        ]
    ].copy()
    predictive_out = predictive[
        [
            "date",
            "close",
            "pre_close",
            "csi300_pct_chg",
            "csi300_log_return",
            "oas_date",
            "oas_percent",
            "oas_bps",
            "oas_change_bps",
            "oas_age_calendar_days",
        ]
    ].copy()

    for frame in (oas_out, csi_out, aligned_out, predictive_out, rolling):
        frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
    predictive_out["oas_date"] = pd.to_datetime(predictive_out["oas_date"]).dt.strftime(
        "%Y-%m-%d"
    )

    oas_out.to_csv(OUT / "BAMLH0A0HYM2_近十年日线.csv", index=False)
    csi_out.to_csv(OUT / "沪深300_近十年日线.csv", index=False)
    aligned_out.to_csv(OUT / "共同日期对齐数据.csv", index=False)
    predictive_out.to_csv(OUT / "前一美股收盘_沪深300对齐数据.csv", index=False)
    rolling.to_csv(OUT / "滚动相关系数.csv", index=False)
    lead_lag.to_csv(OUT / "领先滞后检验.csv", index=False)
    annual.to_csv(OUT / "年度相关系数.csv", index=False)
    subperiod.to_csv(OUT / "分阶段相关系数.csv", index=False)
    pd.DataFrame(adf).to_csv(OUT / "ADF平稳性检验.csv", index=False)
    (OUT / "统计检验结果.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    same_beta_100bp_pct = nw_change["beta"] * 100 * 100
    pred_beta_100bp_pct = nw_predictive["beta"] * 100 * 100
    best_lag = lead_lag.iloc[lead_lag["pearson_r"].abs().idxmax()]
    report = f"""# BAMLH0A0HYM2 与沪深300近十年相关性检验

## 结论

- **水平值相关不应直接用于交易判断。** 同日水平 Pearson `r={level_stats['pearson_r']:.3f}`，但沪深300点位的 ADF `p={adf[1]['p_value']:.4f}`，非平稳；Engle-Granger 协整 `p={results['cointegration']['p_value']:.4f}`。因此水平相关存在伪相关风险。
- **可解释口径是“美国高收益利差日变化 vs 沪深300日收益”。** 同日 Pearson `r={change_stats['pearson_r']:.3f}`（`p{fmt_p(change_stats['pearson_p'])}`），Spearman `ρ={change_stats['spearman_rho']:.3f}`（`p{fmt_p(change_stats['spearman_p'])}`）。利差走阔与沪深300回报负相关。
- Newey-West(HAC, 5 lag) 回归中，每日 OAS 扩大 100bp 对应沪深300同日对数收益约 `{same_beta_100bp_pct:.2f}` 个百分点，`p{fmt_p(nw_change['beta_hac_p'])}`；但同日美国收盘发生在中国收盘之后，**这不是可提前交易的因果信号**。
- 只使用中国开盘前已知的“前一美国收盘”后，Pearson `r={predictive_stats['pearson_r']:.3f}`（`p{fmt_p(predictive_stats['pearson_p'])}`），HAC 回归中每 100bp 对应次日/当日沪深300约 `{pred_beta_100bp_pct:.2f}` 个百分点（`p{fmt_p(nw_predictive['beta_hac_p'])}`）。这才是较接近可用信号的口径。
- 252 个共同交易日滚动相关中位数 `{results['rolling_252']['median']:.3f}`，区间 `{results['rolling_252']['min']:.3f}` 至 `{results['rolling_252']['max']:.3f}`，最新 `{results['rolling_252']['latest']:.3f}`，说明关系会随阶段变化，不能固定套用单一系数。
- 领先滞后 ±5 个共同交易日中，绝对相关最高出现在 `k={int(best_lag['k_common_sessions'])}`，`r={best_lag['pearson_r']:.3f}`，BH 多重检验校正后 `q={best_lag['pearson_q_bh']:.4f}`。定义：`k>0` 表示用 ΔOAS(t) 对应未来第 k 个共同交易日的沪深300收益。

## 数据口径

- 请求区间：2016-07-26 至 2026-07-26。
- BAMLH0A0HYM2 实际区间：{results['actual_ranges']['oas_start']} 至 {results['actual_ranges']['oas_end']}，{results['row_counts']['oas']} 条非空日线；单位为百分比，另换算为 bp。
- 沪深300实际区间：{results['actual_ranges']['csi300_start']} 至 {results['actual_ranges']['csi300_end']}，{results['row_counts']['csi300']} 个交易日；来源为 TuShare `index_daily(000300.SH)`。
- 同日期交集：{results['actual_ranges']['common_start']} 至 {results['actual_ranges']['common_end']}，{results['row_counts']['same_date_common']} 条。
- FRED 自 2026 年 4 月起只公开该 ICE 序列最近 3 年。本数据包用经 SHA256 校验的历史 FRED 镜像补足早期数据，并以当前 FRED 覆盖重叠及最新区间。历史镜像与当前 FRED 重叠 {results['archive_validation']['hf_fred_overlap_rows']} 条，最大绝对差为 {results['archive_validation']['hf_fred_max_abs_diff_pct_points']:.12f} 个百分点；与另一独立归档重叠 {results['archive_validation']['hf_archive_overlap_rows']} 条，最大绝对差同为 {results['archive_validation']['hf_archive_max_abs_diff_pct_points']:.12f}。
- 2026-07-26 为周日，因此两组最新有效日分别停在 2026-07-23（OAS）和 2026-07-24（沪深300），共同样本停在 2026-07-23。

## 检验方法

1. 水平值：Pearson、Spearman、ADF、Engle-Granger 协整。
2. 平稳变换：OAS 一阶差分（bp）与沪深300对数收益。
3. 显著性：常规双侧 p 值、Newey-West HAC 标准误、20 日块自助法 95% 区间。
4. 稳健性：63/252 日滚动相关、年度/分阶段相关、±5 日领先滞后并做 Benjamini-Hochberg 多重检验校正。

## 使用边界

- 相关性不等于因果。BAMLH0A0HYM2 是美国信用风险定价，沪深300还受中国政策、汇率、地产信用、行业权重和跨境资金影响。
- 同日检验有时区错位；若用于盘前风险闸门，应优先用“前一美国收盘”口径，并结合美元、VIX、人民币和中国本土信用指标。
- ICE/BofA 原始指数数据存在许可限制；本包用于个人研究与复核，不建议公开再分发完整历史序列。
"""
    (OUT / "相关性分析报告.md").write_text(report, encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
