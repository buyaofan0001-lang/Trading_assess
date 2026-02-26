#!/usr/bin/env python3
"""Render final trading report from four Chinese subagent outputs.

This tool does not fetch data itself. It validates outputs from:
- 宏观行业研判官
- 空头对抗审计官
- 技术执行判定官
Then composes final markdown report for 总控守门官.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FUNDAMENTAL_KEYS = (
    "收入结构",
    "利润质量",
    "现金流质量",
    "估值与预期",
    "催化财务映射",
)


class ValidationError(Exception):
    """Raised when agent payload fails hard constraints."""


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValidationError(f"JSON root must be object: {path}")
    return data


def require(data: Dict[str, Any], key: str) -> Any:
    if key not in data:
        raise ValidationError(f"Missing required key: {key}")
    return data[key]


def ensure_date(value: str, field: str) -> None:
    if not DATE_RE.match(value):
        raise ValidationError(f"{field} must be YYYY-MM-DD, got: {value}")


def ensure_non_empty_str(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} must be non-empty string")
    return value.strip()


def ensure_list(value: Any, field: str) -> List[Any]:
    if not isinstance(value, list):
        raise ValidationError(f"{field} must be a list")
    return value


def validate_fact_rows(rows: List[Any], min_count: int, prefix: str, label: str) -> Set[str]:
    if len(rows) < min_count:
        raise ValidationError(f"{label} requires >= {min_count} rows, got {len(rows)}")

    source_ids: Set[str] = set()
    for idx, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValidationError(f"{label}[{idx}] must be an object")

        code = ensure_non_empty_str(row.get("编号"), f"{label}[{idx}].编号")
        if not code.startswith(prefix):
            raise ValidationError(f"{label}[{idx}].编号 must start with {prefix}: {code}")

        ensure_non_empty_str(row.get("事实"), f"{label}[{idx}].事实")
        ensure_non_empty_str(row.get("影响"), f"{label}[{idx}].影响")
        date_value = ensure_non_empty_str(row.get("日期"), f"{label}[{idx}].日期")
        ensure_date(date_value, f"{label}[{idx}].日期")

        src = ensure_non_empty_str(row.get("来源编号"), f"{label}[{idx}].来源编号")
        source_ids.add(src)

    return source_ids


def validate_sources(sources: Dict[str, Any], required_ids: Iterable[str], label: str) -> None:
    for src_id in required_ids:
        if src_id not in sources:
            raise ValidationError(f"{label} missing source for id={src_id}")

    for src_id, src in sources.items():
        if not isinstance(src, dict):
            raise ValidationError(f"{label}.{src_id} must be object")

        ensure_non_empty_str(src.get("title"), f"{label}.{src_id}.title")
        ensure_non_empty_str(src.get("url"), f"{label}.{src_id}.url")
        ensure_non_empty_str(src.get("level"), f"{label}.{src_id}.level")

        publish_date = ensure_non_empty_str(src.get("publish_date"), f"{label}.{src_id}.publish_date")
        access_date = ensure_non_empty_str(
            src.get("access_checked_at"), f"{label}.{src_id}.access_checked_at"
        )
        ensure_date(publish_date, f"{label}.{src_id}.publish_date")
        ensure_date(access_date, f"{label}.{src_id}.access_checked_at")


def table_md(rows: List[Dict[str, Any]]) -> str:
    head = "| 编号 | 事实 | 影响 | 日期 | 来源编号 |"
    sep = "| --- | --- | --- | --- | --- |"
    body = [
        f"| {r['编号']} | {r['事实']} | {r['影响']} | {r['日期']} | {r['来源编号']} |"
        for r in rows
    ]
    return "\n".join([head, sep] + body)


def sources_md(sources: Dict[str, Any], used_ids: List[str]) -> str:
    lines: List[str] = []
    for src_id in used_ids:
        src = sources[src_id]
        lines.append(
            "- {id}: {title} | {url} | 级别:{level} | 发布:{publish} | 校验:{check}".format(
                id=src_id,
                title=src["title"],
                url=src["url"],
                level=src["level"],
                publish=src["publish_date"],
                check=src["access_checked_at"],
            )
        )
    return "\n".join(lines)


def render_report(
    plan: Dict[str, Any],
    macro: Dict[str, Any],
    bear: Dict[str, Any],
    technical: Dict[str, Any],
) -> str:
    target = require(plan, "标的")
    strategy = require(plan, "计划")

    macro_list = require(macro, "宏观清单")
    risks = ensure_list(require(macro_list, "风险"), "宏观清单.风险")
    positives = ensure_list(require(macro_list, "利好"), "宏观清单.利好")

    industry = require(macro, "行业深研")
    policy = require(industry, "政策风向")
    prosperity = require(industry, "行业景气度")
    upside = require(industry, "未来增值空间")
    fundamental = require(macro, "基本面分析检查")

    bear_list = require(bear, "空头对抗")
    bear_rows = ensure_list(require(bear_list, "利空"), "空头对抗.利空")
    macro_sources = require(macro, "sources")
    bear_sources = require(bear, "sources")

    tech_part = require(technical, "技术面")
    verify_part = require(technical, "验证")
    script_part = require(technical, "执行与失效")

    title = f"{target['代码']} {target['名称']} 交易验证报告"

    lines: List[str] = [f"# {title}", ""]
    lines += [
        "## 交易输入",
        f"- 市场: {target['市场']}",
        f"- 入场逻辑: {strategy['入场逻辑']}",
        f"- 预期持有周期: {strategy['预期持有周期']}",
        f"- 关键催化: {strategy['关键催化']}",
        f"- 交易类型: {strategy['交易类型']}",
        "",
        "## 1) 事实与对抗",
        "",
        "### 行业深研",
        f"- 政策风向: {policy['结论']}（来源: {', '.join(policy['证据来源'])}）",
        f"- 行业景气度: {prosperity['结论']}（来源: {', '.join(prosperity['证据来源'])}）",
        f"- 未来增值空间: {upside['结论']}（来源: {', '.join(upside['证据来源'])}）",
        "",
        "### 基本面分析检查",
    ]

    for key in FUNDAMENTAL_KEYS:
        node = fundamental[key]
        lines.append(
            f"- {key}: {node['结论']}（来源: {', '.join(node['证据来源'])}; 日期: {node['日期']}; 口径: {node['统计口径']}）"
        )

    lines += [
        "",
        "### 宏观清单（风险）",
        table_md(risks),
        "",
        "来源清单（风险）",
        sources_md(macro_sources, sorted({row["来源编号"] for row in risks})),
        "",
        "### 宏观清单（利好）",
        table_md(positives),
        "",
        "来源清单（利好）",
        sources_md(macro_sources, sorted({row["来源编号"] for row in positives})),
        "",
        "### 空头对抗（利空）",
        table_md(bear_rows),
        "",
        "来源清单（利空）",
        sources_md(bear_sources, sorted({row["来源编号"] for row in bear_rows})),
        "",
        "## 2) 验证结论",
        f"- 技术验证: {verify_part['技术验证']['状态']}；依据: {verify_part['技术验证']['依据']}",
        "- 逻辑证伪条件:",
    ]

    for item in verify_part["逻辑证伪条件"]:
        lines.append(f"  - {item}")

    lines += ["", "## 3) 执行与失效", "- 失效剧本（绑定逻辑证伪条件 + 技术失效条件）:"]

    lines.append("  - 技术失效条件:")
    for item in verify_part["技术失效条件"]:
        lines.append(f"    - {item}")

    lines.append("  - 出场条件:")
    for item in script_part["出场条件"]:
        lines.append(f"    - {item}")

    lines.append("  - 无条件砍仓条件:")
    for item in script_part["无条件砍仓条件"]:
        lines.append(f"    - {item}")

    pattern = tech_part["pattern_and_fail_level"]
    lines += [
        "",
        "## 技术面附注",
        f"- 最新报价: {tech_part['latest_price']}",
        f"- 支撑位: {', '.join(map(str, tech_part['support_levels']))}",
        f"- 阻力位: {', '.join(map(str, tech_part['resistance_levels']))}",
        f"- FVG: {', '.join(tech_part['fvg_zones'])}",
        f"- OB: {', '.join(tech_part['ob_zones'])}",
        f"- 形态: {pattern.get('pattern', '') or '无模式关键位'}",
        f"- 形态失败关键位: {pattern.get('fail_level', '无')}",
    ]

    return "\n".join(lines) + "\n"


def validate_payloads(
    macro: Dict[str, Any],
    bear: Dict[str, Any],
    technical: Dict[str, Any],
) -> None:
    macro_list = require(macro, "宏观清单")
    risks = ensure_list(require(macro_list, "风险"), "宏观清单.风险")
    positives = ensure_list(require(macro_list, "利好"), "宏观清单.利好")

    risk_ids = validate_fact_rows(risks, min_count=8, prefix="R", label="宏观清单.风险")
    pos_ids = validate_fact_rows(positives, min_count=8, prefix="P", label="宏观清单.利好")

    industry = require(macro, "行业深研")
    macro_sources = require(macro, "sources")
    if not isinstance(macro_sources, dict):
        raise ValidationError("macro.sources must be object")

    for k in ("政策风向", "行业景气度", "未来增值空间"):
        node = require(industry, k)
        ensure_non_empty_str(node.get("结论"), f"行业深研.{k}.结论")
        refs = ensure_list(node.get("证据来源"), f"行业深研.{k}.证据来源")
        if not refs:
            raise ValidationError(f"行业深研.{k}.证据来源 cannot be empty")
        for src_id in refs:
            if src_id not in macro_sources:
                raise ValidationError(f"行业深研.{k} 引用了不存在的来源编号: {src_id}")

    fundamental = require(macro, "基本面分析检查")
    if not isinstance(fundamental, dict):
        raise ValidationError("基本面分析检查 must be object")
    for key in FUNDAMENTAL_KEYS:
        node = require(fundamental, key)
        if not isinstance(node, dict):
            raise ValidationError(f"基本面分析检查.{key} must be object")
        ensure_non_empty_str(node.get("结论"), f"基本面分析检查.{key}.结论")
        refs = ensure_list(node.get("证据来源"), f"基本面分析检查.{key}.证据来源")
        if not refs:
            raise ValidationError(f"基本面分析检查.{key}.证据来源 cannot be empty")
        for src_id in refs:
            if src_id not in macro_sources:
                raise ValidationError(f"基本面分析检查.{key} 引用了不存在的来源编号: {src_id}")
        value_date = ensure_non_empty_str(node.get("日期"), f"基本面分析检查.{key}.日期")
        ensure_date(value_date, f"基本面分析检查.{key}.日期")
        ensure_non_empty_str(node.get("统计口径"), f"基本面分析检查.{key}.统计口径")

    validate_sources(macro_sources, required_ids=risk_ids | pos_ids, label="macro.sources")

    bear_list = require(bear, "空头对抗")
    bear_rows = ensure_list(require(bear_list, "利空"), "空头对抗.利空")
    bear_ids = validate_fact_rows(bear_rows, min_count=8, prefix="B", label="空头对抗.利空")

    categories: Set[str] = set()
    for idx, row in enumerate(bear_rows, start=1):
        category = ensure_non_empty_str(row.get("类别"), f"空头对抗.利空[{idx}].类别")
        categories.add(category)

    required_categories = {"财务", "行业景气", "政策", "公司公告"}
    missing = required_categories - categories
    if missing:
        raise ValidationError(f"空头对抗分类覆盖不足，缺少: {sorted(missing)}")

    bear_sources = require(bear, "sources")
    if not isinstance(bear_sources, dict):
        raise ValidationError("bear.sources must be object")
    validate_sources(bear_sources, required_ids=bear_ids, label="bear.sources")

    tech_part = require(technical, "技术面")
    for key in ("latest_price", "support_levels", "resistance_levels", "fvg_zones", "ob_zones"):
        if key not in tech_part:
            raise ValidationError(f"技术面 missing key: {key}")

    verify_part = require(technical, "验证")
    tech_verify = require(verify_part, "技术验证")
    ensure_non_empty_str(tech_verify.get("状态"), "验证.技术验证.状态")
    ensure_non_empty_str(tech_verify.get("依据"), "验证.技术验证.依据")

    logic_invalidations = ensure_list(verify_part.get("逻辑证伪条件"), "验证.逻辑证伪条件")
    tech_invalidations = ensure_list(verify_part.get("技术失效条件"), "验证.技术失效条件")
    if not logic_invalidations:
        raise ValidationError("验证.逻辑证伪条件 cannot be empty")
    if not tech_invalidations:
        raise ValidationError("验证.技术失效条件 cannot be empty")

    script_part = require(technical, "执行与失效")
    exits = ensure_list(script_part.get("出场条件"), "执行与失效.出场条件")
    hard_cuts = ensure_list(script_part.get("无条件砍仓条件"), "执行与失效.无条件砍仓条件")
    if not exits:
        raise ValidationError("执行与失效.出场条件 cannot be empty")
    if not hard_cuts:
        raise ValidationError("执行与失效.无条件砍仓条件 cannot be empty")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build report from four subagent outputs")
    parser.add_argument("--plan", required=True, type=Path, help="Path to plan.json")
    parser.add_argument("--macro", required=True, type=Path, help="Path to macro_industry.json")
    parser.add_argument("--bear", required=True, type=Path, help="Path to bear_case.json")
    parser.add_argument("--technical", required=True, type=Path, help="Path to technical.json")
    parser.add_argument("--output", required=True, type=Path, help="Output markdown path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    plan = load_json(args.plan)
    macro = load_json(args.macro)
    bear = load_json(args.bear)
    technical = load_json(args.technical)

    validate_payloads(macro=macro, bear=bear, technical=technical)
    report = render_report(plan=plan, macro=macro, bear=bear, technical=technical)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] report written to: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
