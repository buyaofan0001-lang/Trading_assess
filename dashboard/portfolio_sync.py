"""Excel-backed positions and local semantic-AI peer discovery.

The historical workbook is not a clean position snapshot, so the engine starts
from a confirmed checkpoint and replays only rows appended after that point.
That makes future changes automatic without allowing known legacy gaps to
reintroduce closed positions.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime
from difflib import SequenceMatcher
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any, Callable

import pandas as pd


REQUIRED_LEDGER_COLUMNS = {
    "证券代码", "证券名称", "买卖标志", "成交日期", "成交价格", "成交数量", "成交金额", "剩余仓位"
}
CODE_ALIASES = {"002208": "002028"}
AI_ENGINE_VERSION = "local-semantic-ai-v3"


def finite(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def normalize_code(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    raw = re.sub(r"\.0$", "", str(value).replace("'", "").strip())
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    code = CODE_ALIASES.get(digits.zfill(6), digits.zfill(6))
    if code.startswith(("4", "8", "92")):
        suffix = "BJ"
    elif code.startswith(("5", "6", "9")):
        suffix = "SH"
    else:
        suffix = "SZ"
    return f"{code}.{suffix}"


def is_fund(ts_code: str) -> bool:
    return ts_code.split(".")[0].startswith(("1", "5"))


def dataframe_digest(frame: pd.DataFrame) -> str:
    raw = frame.to_json(orient="split", date_format="iso", force_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}-", delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, allow_nan=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        temp = Path(handle.name)
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def replay_positions(
    seed_positions: list[dict[str, Any]],
    rows: pd.DataFrame,
    default_hard_stop_pct: float = 0.08,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Replay normalized trades over a confirmed baseline."""
    positions = {item["ts_code"]: deepcopy(item) for item in seed_positions}
    warnings: list[str] = []
    for offset, (_, row) in enumerate(rows.iterrows(), start=1):
        code = normalize_code(row.get("证券代码"))
        action = str(row.get("买卖标志") or "").strip()
        quantity = finite(row.get("成交数量"))
        if not code or not action or quantity is None or quantity == 0:
            warnings.append(f"新增第{offset}行缺少有效代码/方向/数量，已跳过")
            continue
        direction = 1 if "买" in action else -1 if "卖" in action else 0
        if not direction:
            warnings.append(f"新增第{offset}行买卖标志无法识别：{action}")
            continue
        quantity = abs(quantity)
        name = str(row.get("证券名称") or code).strip()
        price = finite(row.get("成交价格"))
        amount = finite(row.get("成交金额"))
        position = positions.get(code)
        if direction > 0:
            if position is None:
                position = {
                    "ts_code": code,
                    "name": name,
                    "shares": 0.0,
                    "avg_cost": 0.0,
                    "total_cost": 0.0,
                    "hard_stop_pct": default_hard_stop_pct,
                    "source": "交易记录.xlsx 自动识别",
                }
                positions[code] = position
            existing_shares = finite(position.get("shares")) or 0.0
            existing_cost = finite(position.get("total_cost"))
            if existing_cost is None:
                existing_cost = (finite(position.get("avg_cost")) or 0.0) * existing_shares
            trade_cost = amount if amount is not None and amount > 0 else (price or 0.0) * quantity
            new_shares = existing_shares + quantity
            new_cost = existing_cost + trade_cost
            position.update({
                "name": name,
                "shares": new_shares,
                "total_cost": new_cost,
                "avg_cost": new_cost / new_shares if new_shares else None,
                "source": "交易记录.xlsx 自动同步",
            })
        else:
            if position is None:
                warnings.append(f"新增第{offset}行卖出{code}，但基准快照中无该持仓")
                continue
            existing_shares = finite(position.get("shares")) or 0.0
            if quantity >= existing_shares - 1e-9:
                positions.pop(code, None)
            else:
                remaining = existing_shares - quantity
                avg_cost = finite(position.get("avg_cost")) or 0.0
                position.update({
                    "shares": remaining,
                    "total_cost": avg_cost * remaining,
                    "source": "交易记录.xlsx 自动同步",
                })

        reported = finite(row.get("剩余仓位"))
        calculated = finite(positions.get(code, {}).get("shares")) or 0.0
        if reported is not None and abs(reported - calculated) > 1e-6:
            warnings.append(
                f"新增第{offset}行{code}表内剩余仓位{reported:g}与基准重放{calculated:g}不一致，采用基准重放"
            )

    result = []
    for item in positions.values():
        shares = finite(item.get("shares")) or 0.0
        if shares <= 0:
            continue
        item["shares"] = int(shares) if shares.is_integer() else shares
        item["shares_display"] = f"{item['shares']:g} {'份' if is_fund(item['ts_code']) else '股'}"
        item["confidence"] = item.get("source") or "Excel自动同步"
        result.append(item)
    result.sort(key=lambda item: item["ts_code"])
    return result, warnings


class LedgerPortfolio:
    def __init__(self, ledger_path: Path, seed_path: Path, settings: dict[str, Any]) -> None:
        self.ledger_path = ledger_path
        self.seed_path = seed_path
        self.settings = settings
        self._lock = threading.Lock()
        self._signature: tuple[int, int] | None = None
        self._payload: dict[str, Any] | None = None

    def load(self) -> dict[str, Any]:
        stat = self.ledger_path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        with self._lock:
            if signature == self._signature and self._payload is not None:
                return deepcopy(self._payload)
            seed = json.loads(self.seed_path.read_text(encoding="utf-8"))
            frame = pd.read_excel(self.ledger_path, sheet_name=seed.get("sheet", "交易记录"))
            missing = sorted(REQUIRED_LEDGER_COLUMNS - set(frame.columns))
            if missing:
                raise RuntimeError(f"交易记录.xlsx 缺少字段：{', '.join(missing)}")
            checkpoint = int(seed["checkpoint_data_rows"])
            if len(frame) < checkpoint:
                raise RuntimeError(f"交易记录.xlsx 当前仅{len(frame)}行，少于持仓基准{checkpoint}行")
            warnings = []
            expected_digest = seed.get("history_digest")
            actual_digest = dataframe_digest(frame.iloc[:checkpoint])
            history_changed = bool(expected_digest and expected_digest != actual_digest)
            if history_changed:
                warnings.append("基准行历史内容发生改动；为避免旧缺口污染，仍从已确认快照重放新增成交")
            holdings, replay_warnings = replay_positions(
                seed.get("positions", []),
                frame.iloc[checkpoint:],
                float(self.settings.get("default_hard_stop_pct", 0.08)),
            )
            warnings.extend(replay_warnings)
            version_source = json.dumps(
                {"signature": signature, "holdings": holdings, "rows": len(frame)},
                ensure_ascii=False,
                sort_keys=True,
            )
            payload = {
                "holdings": holdings,
                "meta": {
                    "source": "交易记录.xlsx",
                    "path": str(self.ledger_path),
                    "modified_at": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
                    "checkpoint_data_rows": checkpoint,
                    "total_data_rows": len(frame),
                    "replayed_rows": len(frame) - checkpoint,
                    "history_changed": history_changed,
                    "warnings": warnings,
                    "portfolio_version": hashlib.sha256(version_source.encode("utf-8")).hexdigest()[:16],
                },
            }
            self._signature = signature
            self._payload = payload
            return deepcopy(payload)


def clean_text(value: Any) -> str:
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", str(value or ""))


def text_features(value: Any) -> set[str]:
    text = clean_text(value)
    return {
        text[index:index + size]
        for size in (2, 3, 4, 5)
        for index in range(max(0, len(text) - size + 1))
    }


def shared_business_phrases(target_text: Any, candidate_text: Any) -> list[str]:
    target = clean_text(target_text)
    candidate = clean_text(candidate_text)
    blocks = sorted(
        SequenceMatcher(None, target, candidate).get_matching_blocks(),
        key=lambda block: block.size,
        reverse=True,
    )
    phrases = []
    for block in blocks:
        if block.size < 4:
            continue
        phrase = target[block.a:block.a + block.size]
        phrase = re.sub(r"^[的与和及为]", "", phrase)
        phrase = re.sub(r"[的与和及为]$", "", phrase)
        if len(phrase) < 4:
            continue
        if phrase and not any(phrase in existing or existing in phrase for existing in phrases):
            phrases.append(phrase)
        if len(phrases) == 2:
            break
    return phrases


def semantic_peer_rank(target: dict[str, Any], candidates: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    target_text = target.get("main_business") or target.get("business_scope") or ""
    target_features = text_features(target_text)
    feature_sets = [target_features] + [text_features(row.get("main_business") or row.get("business_scope") or "") for row in candidates]
    frequencies = Counter(feature for features in feature_sets for feature in features)
    total_documents = len(feature_sets)
    weights = {feature: math.log((total_documents + 1) / (count + 1)) + 1 for feature, count in frequencies.items()}
    target_weight = sum(weights[feature] for feature in target_features) or 1.0
    ranked = []
    for candidate, features in zip(candidates, feature_sets[1:]):
        intersection = target_features & features
        intersection_weight = sum(weights[feature] for feature in intersection)
        candidate_weight = sum(weights[feature] for feature in features) or 1.0
        raw_text = clean_text(candidate.get("main_business") or candidate.get("business_scope") or "")
        length_quality = min(len(raw_text) / 20, 1.0)
        score = 0.60 * intersection_weight / candidate_weight
        score += 0.35 * intersection_weight / target_weight
        score += 0.05 * length_quality
        if len(raw_text) < 8:
            score *= 0.72
        if score < 0.07:
            continue
        selected = shared_business_phrases(target_text, candidate.get("main_business") or candidate.get("business_scope") or "")
        ranked.append({
            "ts_code": candidate["ts_code"],
            "name": candidate["name"],
            "ai_score": round(score, 4),
            "ai_reason": "共同业务语义：" + "、".join(selected or [target.get("industry") or "同产业分类"]),
        })
    ranked.sort(key=lambda item: item["ai_score"], reverse=True)
    return ranked[:limit]


def normalize_benchmark(value: Any) -> str:
    text = clean_text(value)
    return re.sub(r"收益率(?:×?100%)?$", "", text)


class AIPeerResolver:
    def __init__(
        self,
        settings: dict[str, Any],
        library_path: Path,
        universe_cache_path: Path,
        pro_factory: Callable[[], Any],
    ) -> None:
        self.settings = settings
        self.library_path = library_path
        self.universe_cache_path = universe_cache_path
        self.pro_factory = pro_factory
        self._lock = threading.Lock()
        self._universe: dict[str, Any] | None = None

    def _load_library(self) -> dict[str, Any]:
        if not self.library_path.exists():
            return {"version": 1, "engine": AI_ENGINE_VERSION, "updated_at": None, "entries": {}}
        payload = json.loads(self.library_path.read_text(encoding="utf-8"))
        if payload.get("engine") != AI_ENGINE_VERSION:
            payload = {"version": 1, "engine": AI_ENGINE_VERSION, "updated_at": None, "entries": {}}
        return payload

    @staticmethod
    def _json_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
        clean = frame.astype(object).where(pd.notna(frame), None)
        return clean.to_dict("records")

    def _fetch_universe(self) -> dict[str, Any]:
        pro = self.pro_factory()
        basic = pro.stock_basic(
            exchange="", list_status="L", fields="ts_code,name,industry,market,list_date"
        )
        companies = []
        for exchange in ("SSE", "SZSE", "BSE"):
            try:
                companies.append(pro.stock_company(
                    exchange=exchange,
                    fields="ts_code,main_business,business_scope",
                ))
            except Exception:
                continue
        company = pd.concat(companies, ignore_index=True) if companies else pd.DataFrame(columns=["ts_code"])
        stocks = basic.merge(company, on="ts_code", how="left")
        stocks = stocks[~stocks["name"].astype(str).str.contains(r"ST|退", regex=True, na=False)]
        funds = pro.fund_basic(market="E", status="L")
        payload = {
            "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "stocks": self._json_records(stocks),
            "funds": self._json_records(funds),
        }
        atomic_json_write(self.universe_cache_path, payload)
        return payload

    def _get_universe(self) -> dict[str, Any]:
        if self._universe is not None:
            return self._universe
        cache_hours = float(self.settings.get("peer_universe_cache_hours", 24))
        if self.universe_cache_path.exists():
            age = datetime.now().timestamp() - self.universe_cache_path.stat().st_mtime
            if age < cache_hours * 3600:
                self._universe = json.loads(self.universe_cache_path.read_text(encoding="utf-8"))
                return self._universe
        try:
            self._universe = self._fetch_universe()
        except Exception:
            if not self.universe_cache_path.exists():
                raise
            self._universe = json.loads(self.universe_cache_path.read_text(encoding="utf-8"))
        return self._universe

    def _resolve_stock(self, holding: dict[str, Any], universe: dict[str, Any]) -> dict[str, Any]:
        stocks = universe["stocks"]
        target = next((row for row in stocks if row.get("ts_code") == holding["ts_code"]), None)
        if not target:
            return {"peers": [], "benchmark": "产业分类待识别", "cohort_type": "AI自动同行", "confidence": 0, "reason": "市场基础资料缺失"}
        industry = target.get("industry") or "行业未分类"
        candidates = [
            row for row in stocks
            if row.get("ts_code") != holding["ts_code"]
            and row.get("industry") == industry
            and row.get("main_business")
        ]
        peers = semantic_peer_rank(target, candidates, int(self.settings.get("peer_count", 4)))
        average = sum(peer["ai_score"] for peer in peers) / len(peers) if peers else 0.0
        confidence = min(96, round(55 + average * 90)) if peers else 0
        phrases = []
        for peer in peers:
            reason = peer.get("ai_reason", "").removeprefix("共同业务语义：")
            for phrase in reason.split("、"):
                if phrase and phrase not in phrases:
                    phrases.append(phrase)
        reason = f"同属{industry}；业务文本共同指向" + "、".join(phrases[:3]) if peers else f"{industry}内未找到足够相似标的"
        return {
            "peers": peers,
            "benchmark": industry,
            "cohort_type": "AI自动直接同行",
            "confidence": confidence,
            "reason": reason,
        }

    def _resolve_fund(self, holding: dict[str, Any], universe: dict[str, Any]) -> dict[str, Any]:
        funds = universe["funds"]
        target = next((row for row in funds if row.get("ts_code") == holding["ts_code"]), None)
        if not target:
            return {"peers": [], "benchmark": "基金基准待识别", "cohort_type": "AI自动同类基金", "confidence": 0, "reason": "基金基础资料缺失"}
        target_benchmark = normalize_benchmark(target.get("benchmark"))
        target_text = clean_text(f"{target.get('name', '')}{target.get('benchmark', '')}{target.get('invest_type', '')}")
        candidates = []
        for row in funds:
            if row.get("ts_code") == holding["ts_code"]:
                continue
            benchmark = normalize_benchmark(row.get("benchmark"))
            same_benchmark = bool(target_benchmark and benchmark == target_benchmark)
            row_text = clean_text(f"{row.get('name', '')}{row.get('benchmark', '')}{row.get('invest_type', '')}")
            similarity = SequenceMatcher(None, target_text, row_text).ratio()
            score = 1.0 if same_benchmark else similarity * 0.72
            if score < 0.48:
                continue
            candidates.append({
                "ts_code": row["ts_code"],
                "name": row.get("name") or row["ts_code"],
                "ai_score": round(score, 4),
                "ai_reason": f"{'同一业绩基准' if same_benchmark else '基金名称与基准语义相似'}：{target.get('benchmark') or target.get('name')}",
            })
        candidates.sort(key=lambda item: item["ai_score"], reverse=True)
        peers = candidates[:int(self.settings.get("peer_count", 4))]
        confidence = 96 if peers and peers[0]["ai_score"] == 1.0 else (round(55 + (peers[0]["ai_score"] if peers else 0) * 40) if peers else 0)
        return {
            "peers": peers,
            "benchmark": target.get("benchmark") or target.get("name") or "同类基金",
            "cohort_type": "AI自动同指数产品",
            "confidence": confidence,
            "reason": peers[0]["ai_reason"] if peers else "未找到足够相似的上市基金",
        }

    def resolve(self, holding: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            universe = self._get_universe()
            library = self._load_library()
            entry = library["entries"].get(holding["ts_code"])
            if not entry:
                entry = self._resolve_fund(holding, universe) if is_fund(holding["ts_code"]) else self._resolve_stock(holding, universe)
                entry.update({
                    "engine": AI_ENGINE_VERSION,
                    "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                })
                library["entries"][holding["ts_code"]] = entry
                library["updated_at"] = entry["generated_at"]
                atomic_json_write(self.library_path, library)
            enriched = deepcopy(holding)
            enriched.update({
                "peers": entry.get("peers", []),
                "benchmark": entry.get("benchmark") or "AI同行",
                "cohort_type": entry.get("cohort_type") or "AI自动同行",
                "cohort_status": f"AI自动认定 · 置信度 {entry.get('confidence', 0)}%",
                "ai_peer_confidence": entry.get("confidence", 0),
                "ai_peer_reason": entry.get("reason") or "本地语义模型自动判定",
                "ai_peer_engine": entry.get("engine") or AI_ENGINE_VERSION,
            })
            overseas = self.settings.get("overseas_maps", {}).get(holding["ts_code"], [])
            enriched["us_map"] = overseas
            return enriched

    def resolve_all(self, holdings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self.resolve(holding) for holding in holdings]
