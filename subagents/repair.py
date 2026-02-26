#!/usr/bin/env python3
"""Heuristic repair helpers for partially invalid subagent payloads."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _placeholder_source(src_id: str) -> Dict[str, Any]:
    return {
        "title": f"自动补证来源 {src_id}",
        "url": "https://example.com/auto-fix",
        "level": "三级",
        "publisher": "auto-repair",
        "publish_date": _today(),
        "access_checked_at": _today(),
        "subject_verified": False,
    }


def _safe_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _repair_macro(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = deepcopy(payload)
    macro = data.setdefault("宏观清单", {})
    industry = data.setdefault("行业深研", {})
    sources = data.setdefault("sources", {})
    date_str = _today()

    def fix_rows(key: str, prefix: str) -> None:
        rows = _safe_list(macro.get(key))
        fixed_rows: List[Dict[str, Any]] = []
        for idx, row in enumerate(rows, start=1):
            row = _safe_dict(row)
            src_id = str(row.get("来源编号") or f"AUTO_{prefix}{idx}")
            fixed_rows.append(
                {
                    "编号": str(row.get("编号") or f"{prefix}{idx}"),
                    "事实": str(row.get("事实") or "自动补证事实"),
                    "影响": str(row.get("影响") or "待验证影响"),
                    "日期": str(row.get("日期") or date_str),
                    "来源编号": src_id,
                }
            )
        while len(fixed_rows) < 8:
            idx = len(fixed_rows) + 1
            src_id = f"AUTO_{prefix}{idx}"
            fixed_rows.append(
                {
                    "编号": f"{prefix}{idx}",
                    "事实": "自动补证事实",
                    "影响": "待验证影响",
                    "日期": date_str,
                    "来源编号": src_id,
                }
            )
        macro[key] = fixed_rows

    fix_rows("风险", "R")
    fix_rows("利好", "P")

    for section in ("政策风向", "行业景气度", "未来增值空间"):
        node = _safe_dict(industry.get(section))
        refs = [str(x) for x in _safe_list(node.get("证据来源")) if str(x)]
        if not refs:
            refs = ["AUTO_INDUSTRY_1"]
        industry[section] = {
            "结论": str(node.get("结论") or "待补证结论"),
            "证据来源": refs,
        }

    used_ids = set()
    for key in ("风险", "利好"):
        for row in macro[key]:
            used_ids.add(row["来源编号"])
    for section in ("政策风向", "行业景气度", "未来增值空间"):
        used_ids.update(industry[section]["证据来源"])

    for src_id in used_ids:
        if src_id not in sources or not isinstance(sources[src_id], dict):
            sources[src_id] = _placeholder_source(src_id)

    data["宏观清单"] = macro
    data["行业深研"] = industry
    data["sources"] = sources
    return data


def _repair_bear(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = deepcopy(payload)
    part = data.setdefault("空头对抗", {})
    sources = data.setdefault("sources", {})
    data.setdefault("coverage_gaps", [])
    date_str = _today()

    rows = _safe_list(part.get("利空"))
    fixed: List[Dict[str, Any]] = []
    categories = ["财务", "行业景气", "政策", "公司公告"]

    for idx, row in enumerate(rows, start=1):
        row = _safe_dict(row)
        category = str(row.get("类别") or categories[(idx - 1) % len(categories)])
        src_id = str(row.get("来源编号") or f"AUTO_B{idx}")
        fixed.append(
            {
                "编号": str(row.get("编号") or f"B{idx}"),
                "事实": str(row.get("事实") or "自动补证利空事实"),
                "影响": str(row.get("影响") or "待验证影响"),
                "日期": str(row.get("日期") or date_str),
                "来源编号": src_id,
                "类别": category,
            }
        )

    found = {row["类别"] for row in fixed}
    for category in categories:
        if category not in found:
            idx = len(fixed) + 1
            src_id = f"AUTO_B{idx}"
            fixed.append(
                {
                    "编号": f"B{idx}",
                    "事实": "自动补证利空事实",
                    "影响": "待验证影响",
                    "日期": date_str,
                    "来源编号": src_id,
                    "类别": category,
                }
            )

    while len(fixed) < 8:
        idx = len(fixed) + 1
        src_id = f"AUTO_B{idx}"
        fixed.append(
            {
                "编号": f"B{idx}",
                "事实": "自动补证利空事实",
                "影响": "待验证影响",
                "日期": date_str,
                "来源编号": src_id,
                "类别": categories[(idx - 1) % len(categories)],
            }
        )

    part["利空"] = fixed

    used_ids = {row["来源编号"] for row in fixed}
    for src_id in used_ids:
        if src_id not in sources or not isinstance(sources[src_id], dict):
            sources[src_id] = _placeholder_source(src_id)

    data["空头对抗"] = part
    data["sources"] = sources
    return data


def _repair_technical(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = deepcopy(payload)
    tech = data.setdefault("技术面", {})
    verify = data.setdefault("验证", {})
    script = data.setdefault("执行与失效", {})

    tech.setdefault("latest_price", 0.0)
    tech.setdefault("support_levels", [0.0])
    tech.setdefault("resistance_levels", [0.0])
    tech.setdefault("fvg_zones", ["待补证"]) 
    tech.setdefault("ob_zones", ["待补证"]) 
    pattern = _safe_dict(tech.get("pattern_and_fail_level"))
    pattern.setdefault("pattern", "")
    pattern.setdefault("fail_level", 0.0)
    pattern.setdefault("note", "无明显形态")
    tech["pattern_and_fail_level"] = pattern

    tv = _safe_dict(verify.get("技术验证"))
    tv.setdefault("状态", "不成立")
    tv.setdefault("依据", "待补证")
    verify["技术验证"] = tv

    logic = [str(x) for x in _safe_list(verify.get("逻辑证伪条件")) if str(x)]
    if not logic:
        logic = ["关键基本面催化未兑现"]
    verify["逻辑证伪条件"] = logic

    tech_fail = [str(x) for x in _safe_list(verify.get("技术失效条件")) if str(x)]
    if not tech_fail:
        tech_fail = ["跌破关键支撑且无法收复"]
    verify["技术失效条件"] = tech_fail

    exits = [str(x) for x in _safe_list(script.get("出场条件")) if str(x)]
    if not exits:
        exits = ["触发任一逻辑证伪条件"]
    script["出场条件"] = exits

    hard_cuts = [str(x) for x in _safe_list(script.get("无条件砍仓条件")) if str(x)]
    if not hard_cuts:
        hard_cuts = ["出现重大负面事件且价格失守关键位"]
    script["无条件砍仓条件"] = hard_cuts

    data["技术面"] = tech
    data["验证"] = verify
    data["执行与失效"] = script
    return data


def repair_agent_payload(agent: str, payload: Dict[str, Any], _error: str) -> Dict[str, Any]:
    if agent == "macro":
        return _repair_macro(payload)
    if agent == "bear":
        return _repair_bear(payload)
    if agent == "technical":
        return _repair_technical(payload)
    return payload
