#!/usr/bin/env python3
"""Batch industry scoring using component-stock averages.

Formula:
    Score = 0.40 * F + 0.30 * O + 0.20 * M + 0.10 * V

All factors are built from cross-sectional z-scores per date, then mapped to 0-100:
    FactorScore = clip(50 + 15 * factor_z, 0, 100)
"""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


TOP_WEIGHTS: Dict[str, float] = {"F": 0.40, "O": 0.30, "M": 0.20, "V": 0.10}

FACTOR_DEFS: Dict[str, List[Tuple[str, float, bool]]] = {
    # (metric_name, metric_weight_inside_factor, reverse_sign)
    "F": [
        ("f_revenue_yoy", 0.40, False),
        ("f_profit_yoy", 0.40, False),
        ("f_ocf_yoy", 0.20, False),
    ],
    "O": [
        ("o_contract_liab_yoy", 0.45, False),
        ("o_capex_yoy", 0.35, False),
        ("o_inventory_yoy", 0.20, True),  # inventory growth is a drag
    ],
    "M": [
        ("m_return_20d", 0.40, False),
        ("m_breadth_up", 0.30, False),
        ("m_net_inflow_ratio", 0.30, False),
    ],
    "V": [
        ("v_pe_percentile", 1.00, True),  # higher percentile -> lower score
    ],
}

# Stock-level fields expected in input.
INPUT_NUMERIC_FIELDS = [
    "f_revenue_yoy",
    "f_profit_yoy",
    "f_ocf_yoy",
    "o_contract_liab_yoy",
    "o_capex_yoy",
    "o_inventory_yoy",
    "m_return_20d",
    "m_is_up",  # 1/0, used to build m_breadth_up on industry level
    "m_net_inflow_ratio",
    "v_pe_percentile",
]

# Industry-level metrics used for z-scoring.
INDUSTRY_METRICS = [
    "f_revenue_yoy",
    "f_profit_yoy",
    "f_ocf_yoy",
    "o_contract_liab_yoy",
    "o_capex_yoy",
    "o_inventory_yoy",
    "m_return_20d",
    "m_breadth_up",
    "m_net_inflow_ratio",
    "v_pe_percentile",
]


@dataclass
class IndustryRow:
    date: str
    industry: str
    component_count: int
    metrics: Dict[str, Optional[float]]
    z: Dict[str, float]
    factor_z: Dict[str, float]
    factor_score: Dict[str, float]
    score: float
    pass_to_s2: int
    rank: int = 0


def parse_float(value: str) -> Optional[float]:
    if value is None:
        return None
    value = value.strip()
    if value == "":
        return None
    return float(value)


def clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def percentile(values: Sequence[float], p: float) -> float:
    """Linear interpolation percentile. values must be sorted."""
    if not values:
        raise ValueError("Cannot compute percentile on empty sequence")
    if len(values) == 1:
        return values[0]
    pos = (len(values) - 1) * p
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return values[lo]
    left = values[lo]
    right = values[hi]
    return left + (right - left) * (pos - lo)


def factor_score_from_z(value: float) -> float:
    return clip(50.0 + 15.0 * value, 0.0, 100.0)


def require_columns(fieldnames: Iterable[str], required: Iterable[str]) -> None:
    existing = set(fieldnames)
    missing = [c for c in required if c not in existing]
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")


def load_stock_rows(path: str) -> List[Dict[str, Optional[float]]]:
    rows: List[Dict[str, Optional[float]]] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError("Input CSV has no header.")
        require_columns(reader.fieldnames, ["date", "industry", "stock_code"] + INPUT_NUMERIC_FIELDS)

        for raw in reader:
            row: Dict[str, Optional[float]] = {
                "date": raw["date"].strip(),  # type: ignore[assignment]
                "industry": raw["industry"].strip(),  # type: ignore[assignment]
                "stock_code": raw["stock_code"].strip(),  # type: ignore[assignment]
            }
            if not row["date"] or not row["industry"] or not row["stock_code"]:
                continue

            for field in INPUT_NUMERIC_FIELDS:
                row[field] = parse_float(raw.get(field, ""))
            rows.append(row)
    return rows


def aggregate_to_industry(rows: List[Dict[str, Optional[float]]]) -> List[IndustryRow]:
    grouped: Dict[Tuple[str, str], Dict[str, object]] = {}
    for row in rows:
        key = (str(row["date"]), str(row["industry"]))
        if key not in grouped:
            grouped[key] = {
                "stocks": set(),
                "sums": defaultdict(float),
                "counts": defaultdict(int),
                "up_sum": 0.0,
                "up_count": 0,
            }
        state = grouped[key]
        stocks = state["stocks"]
        assert isinstance(stocks, set)
        stocks.add(str(row["stock_code"]))

        sums = state["sums"]
        counts = state["counts"]
        assert isinstance(sums, defaultdict)
        assert isinstance(counts, defaultdict)

        for field in INDUSTRY_METRICS:
            if field == "m_breadth_up":
                continue
            value = row.get(field)
            if value is not None:
                sums[field] += float(value)
                counts[field] += 1

        up_value = row.get("m_is_up")
        if up_value is not None:
            state["up_sum"] += float(up_value)
            state["up_count"] += 1

    out: List[IndustryRow] = []
    for (date, industry), state in grouped.items():
        sums = state["sums"]
        counts = state["counts"]
        assert isinstance(sums, defaultdict)
        assert isinstance(counts, defaultdict)

        metrics: Dict[str, Optional[float]] = {}
        for field in INDUSTRY_METRICS:
            if field == "m_breadth_up":
                up_count = int(state["up_count"])
                metrics[field] = None if up_count == 0 else float(state["up_sum"]) / up_count
            else:
                count = counts[field]
                metrics[field] = None if count == 0 else sums[field] / count

        stocks = state["stocks"]
        assert isinstance(stocks, set)
        out.append(
            IndustryRow(
                date=date,
                industry=industry,
                component_count=len(stocks),
                metrics=metrics,
                z={},
                factor_z={},
                factor_score={},
                score=0.0,
                pass_to_s2=0,
            )
        )

    return out


def winsorized_zscores(values: Dict[int, Optional[float]]) -> Dict[int, float]:
    available = [v for v in values.values() if v is not None]
    if len(available) < 2:
        return {idx: 0.0 for idx in values}

    sorted_vals = sorted(available)
    p05 = percentile(sorted_vals, 0.05)
    p95 = percentile(sorted_vals, 0.95)

    clipped: Dict[int, float] = {}
    for idx, value in values.items():
        if value is None:
            continue
        clipped[idx] = clip(value, p05, p95)

    mean = sum(clipped.values()) / len(clipped)
    variance = sum((v - mean) ** 2 for v in clipped.values()) / len(clipped)
    std = math.sqrt(variance)
    if std == 0:
        return {idx: 0.0 for idx in values}

    zscores: Dict[int, float] = {}
    for idx, value in values.items():
        if value is None:
            zscores[idx] = 0.0
        else:
            zscores[idx] = (clip(value, p05, p95) - mean) / std
    return zscores


def compute_scores(
    industry_rows: List[IndustryRow],
    pass_threshold: float,
    min_components: int,
) -> List[IndustryRow]:
    by_date: Dict[str, List[IndustryRow]] = defaultdict(list)
    for row in industry_rows:
        by_date[row.date].append(row)

    for date, rows in by_date.items():
        # metric z-score
        for metric in INDUSTRY_METRICS:
            cross = {idx: r.metrics.get(metric) for idx, r in enumerate(rows)}
            z_map = winsorized_zscores(cross)
            for idx, z_val in z_map.items():
                rows[idx].z[metric] = z_val

        # factor z, factor score, top score
        for row in rows:
            for factor, defs in FACTOR_DEFS.items():
                sum_z = 0.0
                sum_w = 0.0
                for metric, weight, reverse in defs:
                    z_val = row.z.get(metric, 0.0)
                    sum_z += (-z_val if reverse else z_val) * weight
                    sum_w += weight
                fz = 0.0 if sum_w == 0 else sum_z / sum_w
                row.factor_z[factor] = fz
                row.factor_score[factor] = factor_score_from_z(fz)

            row.score = sum(TOP_WEIGHTS[f] * row.factor_score[f] for f in TOP_WEIGHTS)
            row.pass_to_s2 = int(
                row.score >= pass_threshold
                and row.factor_z.get("F", 0.0) >= 0
                and row.factor_z.get("O", 0.0) >= 0
                and row.component_count >= min_components
            )

        rows.sort(key=lambda r: r.score, reverse=True)
        for i, row in enumerate(rows, start=1):
            row.rank = i

    return industry_rows


def write_output(path: str, rows: List[IndustryRow]) -> None:
    rows = sorted(rows, key=lambda r: (r.date, r.rank))
    fieldnames = [
        "date",
        "industry",
        "rank",
        "component_count",
        "F_score",
        "O_score",
        "M_score",
        "V_score",
        "F_z",
        "O_z",
        "M_z",
        "V_z",
        "industry_prosperity_score",
        "pass_to_s2",
    ]
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(
                {
                    "date": r.date,
                    "industry": r.industry,
                    "rank": r.rank,
                    "component_count": r.component_count,
                    "F_score": f"{r.factor_score['F']:.4f}",
                    "O_score": f"{r.factor_score['O']:.4f}",
                    "M_score": f"{r.factor_score['M']:.4f}",
                    "V_score": f"{r.factor_score['V']:.4f}",
                    "F_z": f"{r.factor_z['F']:.6f}",
                    "O_z": f"{r.factor_z['O']:.6f}",
                    "M_z": f"{r.factor_z['M']:.6f}",
                    "V_z": f"{r.factor_z['V']:.6f}",
                    "industry_prosperity_score": f"{r.score:.4f}",
                    "pass_to_s2": r.pass_to_s2,
                }
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch compute industry prosperity scores.")
    parser.add_argument("--input", required=True, help="Stock-level metrics CSV path")
    parser.add_argument("--output", required=True, help="Industry score CSV output path")
    parser.add_argument("--date", default="", help="Optional filter date, e.g. 2026-01-19")
    parser.add_argument("--pass-threshold", type=float, default=70.0, help="S1 pass threshold")
    parser.add_argument("--min-components", type=int, default=5, help="Minimum component count")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    stock_rows = load_stock_rows(args.input)
    industry_rows = aggregate_to_industry(stock_rows)

    if args.date:
        industry_rows = [r for r in industry_rows if r.date == args.date]
        if not industry_rows:
            raise ValueError(f"No rows found for date: {args.date}")

    industry_rows = compute_scores(
        industry_rows=industry_rows,
        pass_threshold=args.pass_threshold,
        min_components=args.min_components,
    )
    write_output(args.output, industry_rows)

    by_date: Dict[str, List[IndustryRow]] = defaultdict(list)
    for row in industry_rows:
        by_date[row.date].append(row)
    for date in sorted(by_date.keys()):
        rows = sorted(by_date[date], key=lambda r: r.rank)
        print(f"\nDate {date} (top 5):")
        for row in rows[:5]:
            print(
                f"  #{row.rank:<2} {row.industry:<16} "
                f"Score={row.score:6.2f}  Pass={row.pass_to_s2}"
            )
    print(f"\nWrote: {args.output}")


if __name__ == "__main__":
    main()
