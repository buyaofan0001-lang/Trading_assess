#!/usr/bin/env python3
"""Validation helpers for plan and subagent payloads."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any, Dict, List

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ValidationError(Exception):
    """Raised when payload fails schema or business checks."""


def _ensure_dict(value: Any, field: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{field} must be object")
    return value


def _ensure_list(value: Any, field: str) -> List[Any]:
    if not isinstance(value, list):
        raise ValidationError(f"{field} must be list")
    return value


def _ensure_str(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} must be non-empty string")
    return value.strip()


def _ensure_date(value: Any, field: str) -> str:
    value = _ensure_str(value, field)
    if not DATE_RE.match(value):
        raise ValidationError(f"{field} must be YYYY-MM-DD")
    return value


def validate_plan(plan: Dict[str, Any]) -> None:
    target = _ensure_dict(plan.get("标的"), "标的")
    strategy = _ensure_dict(plan.get("计划"), "计划")

    for field in ("代码", "名称", "市场"):
        _ensure_str(target.get(field), f"标的.{field}")
    for field in ("入场逻辑", "预期持有周期", "关键催化", "交易类型"):
        _ensure_str(strategy.get(field), f"计划.{field}")


def _validate_fact_rows(rows: List[Any], min_count: int, prefix: str, label: str) -> None:
    if len(rows) < min_count:
        raise ValidationError(f"{label} requires >= {min_count} rows")
    for i, row in enumerate(rows, start=1):
        row = _ensure_dict(row, f"{label}[{i}]")
        code = _ensure_str(row.get("编号"), f"{label}[{i}].编号")
        if not code.startswith(prefix):
            raise ValidationError(f"{label}[{i}].编号 must start with {prefix}")
        _ensure_str(row.get("事实"), f"{label}[{i}].事实")
        _ensure_str(row.get("影响"), f"{label}[{i}].影响")
        _ensure_date(row.get("日期"), f"{label}[{i}].日期")
        _ensure_str(row.get("来源编号"), f"{label}[{i}].来源编号")


def validate_macro_payload(payload: Dict[str, Any]) -> None:
    macro_list = _ensure_dict(payload.get("宏观清单"), "宏观清单")
    risks = _ensure_list(macro_list.get("风险"), "宏观清单.风险")
    positives = _ensure_list(macro_list.get("利好"), "宏观清单.利好")

    _validate_fact_rows(risks, 8, "R", "宏观清单.风险")
    _validate_fact_rows(positives, 8, "P", "宏观清单.利好")

    industry = _ensure_dict(payload.get("行业深研"), "行业深研")
    for field in ("政策风向", "行业景气度", "未来增值空间"):
        node = _ensure_dict(industry.get(field), f"行业深研.{field}")
        _ensure_str(node.get("结论"), f"行业深研.{field}.结论")
        refs = _ensure_list(node.get("证据来源"), f"行业深研.{field}.证据来源")
        if not refs:
            raise ValidationError(f"行业深研.{field}.证据来源 cannot be empty")

    sources = _ensure_dict(payload.get("sources"), "sources")
    if not sources:
        raise ValidationError("sources cannot be empty")


def validate_bear_payload(payload: Dict[str, Any]) -> None:
    bear_part = _ensure_dict(payload.get("空头对抗"), "空头对抗")
    rows = _ensure_list(bear_part.get("利空"), "空头对抗.利空")
    _validate_fact_rows(rows, 8, "B", "空头对抗.利空")

    required_categories = {"财务", "行业景气", "政策", "公司公告"}
    found = set()
    for i, row in enumerate(rows, start=1):
        category = _ensure_str(row.get("类别"), f"空头对抗.利空[{i}].类别")
        found.add(category)

    missing = required_categories - found
    if missing:
        raise ValidationError(f"空头对抗分类覆盖不足: {sorted(missing)}")

    _ensure_list(payload.get("coverage_gaps", []), "coverage_gaps")
    sources = _ensure_dict(payload.get("sources"), "sources")
    if not sources:
        raise ValidationError("sources cannot be empty")


def validate_technical_payload(payload: Dict[str, Any]) -> None:
    tech = _ensure_dict(payload.get("技术面"), "技术面")
    for key in ("latest_price", "support_levels", "resistance_levels", "fvg_zones", "ob_zones"):
        if key not in tech:
            raise ValidationError(f"技术面 missing key: {key}")

    verify = _ensure_dict(payload.get("验证"), "验证")
    tv = _ensure_dict(verify.get("技术验证"), "验证.技术验证")
    _ensure_str(tv.get("状态"), "验证.技术验证.状态")
    _ensure_str(tv.get("依据"), "验证.技术验证.依据")

    logic = _ensure_list(verify.get("逻辑证伪条件"), "验证.逻辑证伪条件")
    tech_fail = _ensure_list(verify.get("技术失效条件"), "验证.技术失效条件")
    if not logic:
        raise ValidationError("验证.逻辑证伪条件 cannot be empty")
    if not tech_fail:
        raise ValidationError("验证.技术失效条件 cannot be empty")

    script = _ensure_dict(payload.get("执行与失效"), "执行与失效")
    exits = _ensure_list(script.get("出场条件"), "执行与失效.出场条件")
    hard_cuts = _ensure_list(script.get("无条件砍仓条件"), "执行与失效.无条件砍仓条件")
    if not exits:
        raise ValidationError("执行与失效.出场条件 cannot be empty")
    if not hard_cuts:
        raise ValidationError("执行与失效.无条件砍仓条件 cannot be empty")


def validate_agent_output(agent: str, payload: Dict[str, Any]) -> None:
    if agent == "macro":
        validate_macro_payload(payload)
        return
    if agent == "bear":
        validate_bear_payload(payload)
        return
    if agent == "technical":
        validate_technical_payload(payload)
        return
    raise ValidationError(f"Unknown agent: {agent}")


def validate_final_payloads(
    subagents_dir: Path,
    macro_payload: Dict[str, Any],
    bear_payload: Dict[str, Any],
    technical_payload: Dict[str, Any],
) -> None:
    orchestrate_path = subagents_dir / "orchestrate_report.py"
    spec = importlib.util.spec_from_file_location("orchestrate_report", orchestrate_path)
    if spec is None or spec.loader is None:
        raise ValidationError(f"Unable to load orchestrator at {orchestrate_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.validate_payloads(
        macro=macro_payload,
        bear=bear_payload,
        technical=technical_payload,
    )
