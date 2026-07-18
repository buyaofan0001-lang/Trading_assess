#!/usr/bin/env python3
"""Trading Control dashboard server.

Serves a zero-build frontend and a JSON API backed by the repository's
canonical Tushare client plus yfinance for the last completed US session.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import math
import os
import re
import statistics
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pandas as pd
import akshare as ak
import yfinance as yf

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))

from tushare_client import get_pro  # noqa: E402
from portfolio_sync import AI_ENGINE_VERSION, AIPeerResolver, LedgerPortfolio  # noqa: E402

CONFIG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
LEDGER = LedgerPortfolio(
    (HERE / CONFIG["ledger_path"]).resolve(),
    (HERE / CONFIG["position_seed"]).resolve(),
    CONFIG,
)
AI_PEERS = AIPeerResolver(
    CONFIG,
    (HERE / CONFIG["peer_library"]).resolve(),
    (HERE / CONFIG["peer_universe_cache"]).resolve(),
    get_pro,
)
PERIOD_DAYS = {"1d": 1, "5d": 5, "20d": 20}
INTRADAY_TTL_SECONDS = 60
JOURNAL_DIR = REPO / "日记"
JOURNAL_NAME_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})\.md$")
JOURNAL_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
JOURNAL_MAX_BYTES = 512_000
REPORT_ROOT = Path(CONFIG["daily_report_root"]).expanduser().resolve()
REPORT_KINDS = {
    "premarket": {"label": "昨夜盘前报告", "relative": Path("logs/pre-market")},
    "close": {"label": "今日收盘复盘", "relative": Path("logs")},
}
REPORT_MAX_BYTES = 2_000_000
RISK_MODEL_ROOT = Path(CONFIG["risk_model_root"]).expanduser().resolve()
RISK_MODEL_MAX_BYTES = 2_000_000
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

RISK_FEATURE_LABELS = {
    "tech_vol60": "科技60日波动率",
    "tech_vol20": "科技20日波动率",
    "tech_vol5": "科技5日波动率",
    "tech_downside_vol20": "科技20日下行波动率",
    "tech_ret1": "科技1日涨跌",
    "tech_ret2": "科技2日涨跌",
    "tech_ret5": "科技5日涨跌",
    "tech_ret10": "科技10日涨跌",
    "tech_ret20": "科技20日涨跌",
    "tech_breadth_ret20_positive": "科技成分20日上涨广度",
    "medicine_relative_tech_5": "医药相对科技5日强弱",
    "medicine_relative_tech_20": "医药相对科技20日强弱",
    "old_style_relative_tech_5": "防御风格相对科技5日强弱",
    "old_style_relative_tech_20": "防御风格相对科技20日强弱",
    "old_style_relative_tech_60": "防御风格相对科技60日强弱",
    "finance_relative_tech_5": "金融相对科技5日强弱",
    "finance_relative_tech_20": "金融相对科技20日强弱",
    "csi300_vol20": "沪深300的20日波动率",
    "csi300_ret5": "沪深300的5日涨跌",
    "csi300_ret20": "沪深300的20日涨跌",
    "chinext_ret5": "创业板5日涨跌",
    "chinext_ret20": "创业板20日涨跌",
    "qqq_ret1": "隔夜QQQ单日涨跌",
    "qqq_ret3": "隔夜QQQ三日涨跌",
    "qqq_ret5": "隔夜QQQ五日涨跌",
    "us_semis_ret1": "海外半导体单日涨跌",
    "us_semis_ret3": "海外半导体三日涨跌",
    "us_semis_ret5": "海外半导体五日涨跌",
    "vix_z60": "VIX相对60日异常度",
    "vvix_z60": "VVIX相对60日异常度",
    "vix_term_9d_3m": "VIX九日/三月期限结构",
    "usdcnh_ret5": "离岸人民币五日变化",
    "dxy_ret5": "美元指数五日变化",
    "yield_curve_10y2y": "美债10年/2年期限利差",
    "y2_chg5": "美国2年期收益率五日变化",
}


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
INTRADAY_PRIMARY_LOCK = threading.Lock()
INTRADAY_PRIMARY: dict[str, dict[str, dict[str, Any]]] = {}
INTRADAY_REFRESHING = False
INTRADAY_LAST_STARTED = 0.0
INTRADAY_PRIMARY_ERRORS: list[str] = []


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


def portfolio_snapshot(errors: list[str] | None = None) -> dict[str, Any]:
    """Load the latest Excel-backed portfolio and attach AI-generated peers."""
    payload = LEDGER.load()
    try:
        payload["holdings"] = AI_PEERS.resolve_all(payload["holdings"])
    except Exception as exc:
        if errors is not None:
            errors.append(f"AI同行识别：{exc}")
        for holding in payload["holdings"]:
            holding.update({
                "peers": [],
                "benchmark": "AI同行数据暂不可用",
                "cohort_type": "AI自动同行",
                "cohort_status": "AI识别暂不可用",
                "ai_peer_confidence": 0,
                "ai_peer_reason": str(exc),
                "ai_peer_engine": AI_ENGINE_VERSION,
                "us_map": [],
            })
    return payload


def validate_journal_date(value: Any) -> str:
    if not isinstance(value, str) or not JOURNAL_DATE_RE.fullmatch(value):
        raise ValueError("日记日期必须为 YYYY-MM-DD")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("日记日期无效") from exc
    if parsed.strftime("%Y-%m-%d") != value:
        raise ValueError("日记日期无效")
    return value


def journal_date_from_path(path: Path) -> str | None:
    match = JOURNAL_NAME_RE.fullmatch(path.name)
    if not match:
        return None
    try:
        parsed = datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None
    return parsed.strftime("%Y-%m-%d")


def journal_file_map() -> dict[str, Path]:
    """Map normalized dates to local Markdown files without following symlinks."""
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {}
    for path in JOURNAL_DIR.iterdir():
        if path.is_symlink() or not path.is_file():
            continue
        date = journal_date_from_path(path)
        if not date:
            continue
        current = files.get(date)
        if current is None or path.name == f"{date}.md":
            files[date] = path
    return files


def journal_metadata(path: Path, date: str) -> dict[str, Any]:
    content = path.read_text(encoding="utf-8")
    excerpt = re.sub(r"\s+", " ", content).strip()
    stat = path.stat()
    return {
        "date": date,
        "filename": path.name,
        "modified_at": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
        "chars": len(content),
        "excerpt": excerpt[:96],
    }


def list_journals() -> dict[str, Any]:
    files = journal_file_map()
    entries = [journal_metadata(path, date) for date, path in files.items()]
    entries.sort(key=lambda item: item["date"], reverse=True)
    return {
        "journals": entries,
        "today": datetime.now().strftime("%Y-%m-%d"),
        "folder": str(JOURNAL_DIR),
    }


def get_journal(date: str) -> dict[str, Any]:
    date = validate_journal_date(date)
    path = journal_file_map().get(date)
    if path is None:
        return {"date": date, "filename": f"{date}.md", "content": "", "exists": False}
    payload = journal_metadata(path, date)
    payload.update({"content": path.read_text(encoding="utf-8"), "exists": True})
    return payload


def save_journal(date: str, content: Any) -> dict[str, Any]:
    date = validate_journal_date(date)
    if not isinstance(content, str):
        raise ValueError("日记内容必须为文本")
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    encoded = normalized.encode("utf-8")
    if len(encoded) > JOURNAL_MAX_BYTES:
        raise ValueError("日记内容不能超过 500KB")

    files = journal_file_map()
    target = files.get(date, JOURNAL_DIR / f"{date}.md")
    existed = target.exists()
    with tempfile.NamedTemporaryFile("wb", dir=JOURNAL_DIR, prefix=".journal-", delete=False) as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
        temp_path = Path(handle.name)
    try:
        os.replace(temp_path, target)
    finally:
        temp_path.unlink(missing_ok=True)
    payload = journal_metadata(target, date)
    payload.update({"content": normalized, "exists": True, "created": not existed})
    return payload


def validate_report_kind(value: Any) -> str:
    if not isinstance(value, str) or value not in REPORT_KINDS:
        raise ValueError("报告类型必须为 premarket 或 close")
    return value


def report_file_map(kind: str, root: Path | None = None) -> dict[str, Path]:
    """Discover generated reports without following links or leaving the report root."""
    kind = validate_report_kind(kind)
    base = (root or REPORT_ROOT) / REPORT_KINDS[kind]["relative"]
    if not base.exists() or base.is_symlink() or not base.is_dir():
        return {}
    files: dict[str, Path] = {}
    for year_dir in base.iterdir():
        if year_dir.is_symlink() or not year_dir.is_dir() or not re.fullmatch(r"\d{4}", year_dir.name):
            continue
        for path in year_dir.iterdir():
            if path.is_symlink() or not path.is_file():
                continue
            date = journal_date_from_path(path)
            if date and date.startswith(f"{year_dir.name}-"):
                files[date] = path
    return files


def report_metadata(path: Path, date: str, kind: str) -> dict[str, Any]:
    stat = path.stat()
    if stat.st_size > REPORT_MAX_BYTES:
        raise ValueError(f"报告文件过大：{path.name}")
    content = path.read_text(encoding="utf-8")
    title_match = re.search(r"^#\s+(.+)$", content, flags=re.MULTILINE)
    plain = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", content)
    plain = re.sub(r"[#>*_`|\-]+", " ", plain)
    excerpt = re.sub(r"\s+", " ", plain).strip()
    return {
        "kind": kind,
        "date": date,
        "filename": path.name,
        "title": title_match.group(1).strip() if title_match else REPORT_KINDS[kind]["label"],
        "modified_at": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
        "chars": len(content),
        "excerpt": excerpt[:120],
        "version": f"{stat.st_mtime_ns:x}-{stat.st_size:x}",
    }


def report_health(kind: str, latest: str | None, now: datetime | None = None) -> dict[str, Any]:
    current = (now or datetime.now(SHANGHAI_TZ)).astimezone(SHANGHAI_TZ)
    today = current.strftime("%Y-%m-%d")
    weekday = current.weekday() < 5
    due_minute = 9 * 60 if kind == "premarket" else 17 * 60
    due = weekday and current.hour * 60 + current.minute >= due_minute
    stale = due and latest != today
    if stale:
        shown = f"当前展示 {latest}" if latest else "当前没有可用报告"
        message = f"{today} 报告尚未生成；{shown}。若今天是交易日，请检查每日任务。"
    elif latest == today:
        message = f"{today} 报告已生成"
    else:
        message = "尚未到今日报告的计划生成时间"
    return {
        "today": today,
        "due": due,
        "stale": stale,
        "status": "missing" if stale else "current" if latest == today else "not_due",
        "message": message,
    }


def list_daily_reports(root: Path | None = None, now: datetime | None = None) -> dict[str, Any]:
    report_root = root or REPORT_ROOT
    reports: dict[str, Any] = {}
    version_parts = []
    for kind, settings in REPORT_KINDS.items():
        files = report_file_map(kind, report_root)
        entries = [report_metadata(path, date, kind) for date, path in files.items()]
        entries.sort(key=lambda item: item["date"], reverse=True)
        version_parts.extend(f"{kind}:{item['date']}:{item['version']}" for item in entries)
        reports[kind] = {
            "label": settings["label"],
            "folder": str(report_root / settings["relative"]),
            "latest": entries[0]["date"] if entries else None,
            "items": entries,
        }
        reports[kind]["health"] = report_health(kind, reports[kind]["latest"], now)
    digest = hashlib.sha256("|".join(version_parts).encode("utf-8")).hexdigest()[:16]
    return {
        "root": str(report_root),
        "library_version": digest,
        "generated_at": iso_now(),
        "reports": reports,
    }


def get_daily_report(kind: str, date: str, root: Path | None = None) -> dict[str, Any]:
    kind = validate_report_kind(kind)
    date = validate_journal_date(date)
    path = report_file_map(kind, root).get(date)
    if path is None:
        raise ValueError(f"{date} 暂无{REPORT_KINDS[kind]['label']}")
    payload = report_metadata(path, date, kind)
    payload.update({"content": path.read_text(encoding="utf-8"), "exists": True})
    return payload


def risk_feature_label(feature: str) -> str:
    if feature in RISK_FEATURE_LABELS:
        return RISK_FEATURE_LABELS[feature]
    patterns = (
        (r"^tech_ret(\d+)$", "科技{}日涨跌"),
        (r"^tech_vol(\d+)$", "科技{}日波动率"),
        (r"^tech_dd(\d+)$", "科技{}日回撤"),
        (r"^tech_above_ma(\d+)$", "科技位于{}日均线上方"),
        (r"^tech_breadth_ret(\d+)_positive$", "科技成分{}日上涨广度"),
        (r"^medicine_relative_tech_(\d+)$", "医药相对科技{}日强弱"),
        (r"^finance_relative_tech_(\d+)$", "金融相对科技{}日强弱"),
        (r"^old_style_relative_tech_(\d+)$", "防御风格相对科技{}日强弱"),
        (r"^csi300_ret(\d+)$", "沪深300的{}日涨跌"),
        (r"^chinext_ret(\d+)$", "创业板{}日涨跌"),
        (r"^qqq_ret(\d+)$", "隔夜QQQ的{}日涨跌"),
        (r"^us_semis_ret(\d+)$", "海外半导体{}日涨跌"),
    )
    for pattern, template in patterns:
        match = re.fullmatch(pattern, feature)
        if match:
            return template.format(match.group(1))
    return feature


def _read_risk_json(path: Path) -> dict[str, Any]:
    if not path.exists() or path.is_symlink() or not path.is_file():
        raise FileNotFoundError(f"缺少模型输出：{path.name}")
    if path.stat().st_size > RISK_MODEL_MAX_BYTES:
        raise ValueError(f"模型输出过大：{path.name}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"模型输出格式无效：{path.name}")
    return payload


def _risk_factor(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict) or not item.get("feature"):
        return None
    feature = str(item["feature"])
    return {
        "feature": feature,
        "label": risk_feature_label(feature),
        "value": finite(item.get("value")),
        "percentile": finite(item.get("training_percentile")),
        "contribution": finite(item.get("score_contribution")),
    }


def _risk_status(brief: dict[str, Any]) -> tuple[str, str]:
    if not bool(brief.get("data_freshness_pass")):
        return "数据过期 · 只作诊断", "stale"
    tier = str(brief.get("raw_warning_tier") or "normal")
    if bool(brief.get("operational_new_warning")):
        return ("高风险新预警", "high") if tier == "high" else ("新风险预警", "warning")
    if "already_weak" in str(brief.get("display_state") or brief.get("controller_state") or ""):
        return "已处弱势 · 诊断", "diagnostic"
    if tier == "watch":
        return "风险观察", "watch"
    return "未触发新预警", "normal"


def read_risk_model(root: Path | None = None) -> dict[str, Any]:
    """Read the frozen model's official compact outputs without rerunning it."""
    model_root = (root or RISK_MODEL_ROOT).expanduser().resolve()
    results = model_root / "results"
    brief_path = results / "current_warning_brief.json"
    shadow_path = results / "forward_shadow_summary.csv"
    manifest_path = model_root / "config" / "frozen_model_manifest.json"
    try:
        brief = _read_risk_json(brief_path)
        manifest = _read_risk_json(manifest_path)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        return {
            "available": False,
            "generated_at": iso_now(),
            "root": str(model_root),
            "error": str(exc),
        }

    shadow: dict[str, Any] = {}
    if shadow_path.exists() and shadow_path.is_file() and not shadow_path.is_symlink():
        try:
            frame = pd.read_csv(shadow_path)
            if not frame.empty:
                row = frame.iloc[0]
                shadow = {
                    "status": str(row.get("status") or brief.get("shadow_validation_status") or "collecting"),
                    "matured_rows": int(finite(row.get("matured_eligible_rows")) or 0),
                    "required_rows": int(finite(row.get("minimum_rows_required")) or 252),
                    "positive_events": int(finite(row.get("positive_events")) or 0),
                    "required_positive_events": int(finite(row.get("minimum_positive_events_required")) or 25),
                    "promotion_pass": bool(row.get("promotion_pass")) if not pd.isna(row.get("promotion_pass")) else False,
                }
        except Exception:
            shadow = {}
    if not shadow:
        shadow = {
            "status": str(brief.get("shadow_validation_status") or "collecting"),
            "matured_rows": 0,
            "required_rows": 252,
            "positive_events": 0,
            "required_positive_events": 25,
            "promotion_pass": False,
        }

    specification = manifest.get("model_specification") or {}
    thresholds = specification.get("signal") or {}
    status_label, status_code = _risk_status(brief)
    files = [brief_path, shadow_path, manifest_path]
    version_input = []
    modified_times = []
    for path in files:
        if path.exists() and path.is_file():
            stat = path.stat()
            version_input.append(f"{path.name}:{stat.st_mtime_ns}:{stat.st_size}")
            modified_times.append(stat.st_mtime)
    version = hashlib.sha256("|".join(version_input).encode("utf-8")).hexdigest()[:16]
    up = [_risk_factor(item) for item in brief.get("top_risk_up_factors", [])]
    down = [_risk_factor(item) for item in brief.get("top_risk_down_factors", [])]
    return {
        "available": True,
        "generated_at": iso_now(),
        "version": version,
        "root": str(model_root),
        "result_modified_at": datetime.fromtimestamp(max(modified_times)).astimezone().isoformat(timespec="seconds") if modified_times else None,
        "signal_date": brief.get("signal_date"),
        "status": {"label": status_label, "code": status_code},
        "scores": {
            "raw": finite(brief.get("raw_warning_score")),
            "calibrated_probability": finite(brief.get("calibrated_selloff_probability")),
            "tier": str(brief.get("raw_warning_tier") or "normal"),
            "warning_threshold": finite(thresholds.get("warning_threshold")) or 0.55,
            "high_threshold": finite(thresholds.get("high_warning_threshold")) or 0.70,
        },
        "operational": {
            "new_warning": bool(brief.get("operational_new_warning")),
            "warning_eligible": bool(brief.get("warning_eligible")),
            "automatic_position_action_available": bool(brief.get("automatic_position_action_available")),
            "message": str(brief.get("message") or "模型未提供说明。"),
        },
        "freshness": {
            "status": str(brief.get("data_freshness_status") or "unknown"),
            "pass": bool(brief.get("data_freshness_pass")),
            "stale_symbols": [str(item) for item in brief.get("stale_data_symbols", [])],
        },
        "model": {
            "version": str(brief.get("model_version") or manifest.get("model_version") or "unknown"),
            "fingerprint": str(brief.get("model_specification_sha256") or manifest.get("model_specification_sha256") or ""),
            "deployment_status": "research_only",
            "scope": "科技板块未来1–2日风险起点；不是个股预测，也不是自动买卖指令。",
        },
        "shadow": shadow,
        "factors": {
            "up": [item for item in up if item is not None][:5],
            "down": [item for item in down if item is not None][:5],
        },
    }


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
            "market_date": str(row.get("trade_date") or datetime.now(SHANGHAI_TZ).strftime("%Y%m%d")),
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


def compute_volatility_metrics(
    daily: pd.DataFrame,
    live: dict[str, Any] | None,
    atr_days: int = 14,
    vol_days: int = 20,
) -> dict[str, Any]:
    """Measure today's downside against a prior-session Wilder ATR boundary."""
    if daily is None or daily.empty:
        raise ValueError("缺少日线历史")
    work = daily.sort_values("trade_date").copy()
    evaluation_date = re.sub(r"\D", "", str((live or {}).get("market_date") or ""))[:8]
    if evaluation_date and "trade_date" in work.columns and not work.empty:
        last_date = re.sub(r"\D", "", str(work.iloc[-1].get("trade_date") or ""))[:8]
        if last_date == evaluation_date:
            work = work.iloc[:-1]

    true_ranges: list[float] = []
    returns: list[float] = []
    for _, row in work.iterrows():
        high = finite(row.get("high"))
        low = finite(row.get("low"))
        pre_close = finite(row.get("pre_close"))
        close = finite(row.get("close"))
        if high is not None and low is not None and pre_close:
            true_ranges.append(max(high - low, abs(high - pre_close), abs(low - pre_close)))
        if close is not None and pre_close:
            returns.append(close / pre_close - 1)

    if len(true_ranges) < atr_days:
        raise ValueError(f"不足{atr_days}个完整交易日")
    atr = statistics.mean(true_ranges[:atr_days])
    for true_range in true_ranges[atr_days:]:
        atr = (atr * (atr_days - 1) + true_range) / atr_days

    pre_close = finite((live or {}).get("pre_close"))
    if pre_close is None and not work.empty:
        pre_close = finite(work.iloc[-1].get("close"))
    price = finite((live or {}).get("price"))
    current_return = finite((live or {}).get("return"))
    if current_return is None and price is not None and pre_close:
        current_return = price / pre_close - 1
    low = finite((live or {}).get("low"))
    atr_pct = atr / pre_close if pre_close else None
    current_down_atr = max(0.0, pre_close - price) / atr if pre_close and price is not None and atr else None
    intraday_low_atr = max(0.0, pre_close - low) / atr if pre_close and low is not None and atr else None

    recent_returns = returns[-vol_days:]
    realized_vol = statistics.stdev(recent_returns) * math.sqrt(252) if len(recent_returns) >= 10 else None
    if current_return is None or current_down_atr is None:
        status, status_code = "数据不足", "insufficient"
    elif current_return >= 0:
        status, status_code = "今日未下跌", "up"
    elif current_down_atr <= 1.0:
        status, status_code = "正常范围内", "normal"
    elif current_down_atr <= 1.5:
        status, status_code = "波动偏大", "elevated"
    else:
        status, status_code = "超出正常范围", "extreme"

    return {
        "atr_days": atr_days,
        "atr_value": atr,
        "atr_pct": atr_pct,
        "realized_vol_days": vol_days,
        "realized_vol_annualized": realized_vol,
        "current_down_atr": current_down_atr,
        "intraday_low_atr": intraday_low_atr,
        "status": status,
        "status_code": status_code,
        "history_end": str(work.iloc[-1].get("trade_date") or "") if not work.empty else None,
    }


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
    volatility = None
    if "shares" in item:
        try:
            volatility = compute_volatility_metrics(history(ts_code), live)
        except Exception as exc:
            errors.append(f"{ts_code} ATR/波动率：{exc}")
    return {
        "ts_code": ts_code,
        "name": item.get("name") or (live or {}).get("name") or ts_code,
        "price": finite((live or {}).get("price")),
        "pre_close": finite((live or {}).get("pre_close")),
        "return": ret,
        "vs_vwap": finite((live or {}).get("vs_vwap")),
        "turnover_intensity": intensity,
        "volatility": volatility,
        "source": (live or {}).get("source", "Tushare 日线"),
        "ai_score": item.get("ai_score"),
        "ai_reason": item.get("ai_reason"),
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
            "hard_stop_pct": finite(holding.get("hard_stop_pct")),
            "pnl_pct": pnl_pct,
            "pnl_amount": pnl_amount,
            "confidence": holding.get("confidence"),
        },
        "peers": peer_rows,
        "cohort_type": holding["cohort_type"],
        "benchmark": holding["benchmark"],
        "cohort_status": holding.get("cohort_status", "AI自动认定"),
        "ai_peer_confidence": holding.get("ai_peer_confidence"),
        "ai_peer_reason": holding.get("ai_peer_reason"),
        "ai_peer_engine": holding.get("ai_peer_engine"),
        "peer_median": peer_median,
        "excess": excess,
        "rank": rank,
        "member_count": len(valid_all),
        "percentile": percentile,
        "strength": strength,
        "matrix": matrix,
    }


def build_groups(timeframe: str, errors: list[str], holdings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fetch every unique cohort member concurrently, then score groups locally."""
    items: dict[str, dict[str, Any]] = {}
    for holding in holdings:
        items[holding["ts_code"]] = holding
        for peer in holding["peers"]:
            items[peer["ts_code"]] = peer

    if not items:
        return []
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
                    "volatility": None,
                    "source": "数据暂不可用",
                }

    return [
        score_group(
            holding,
            snapshots[holding["ts_code"]],
            [snapshots[peer["ts_code"]] for peer in holding["peers"]],
        )
        for holding in holdings
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


def eastmoney_intraday_points(item: dict[str, Any], trade_date: str) -> list[dict[str, Any]]:
    ts_code = item["ts_code"]
    endpoint = ak.fund_etf_hist_min_em if ts_code.startswith(("5", "1")) else ak.stock_zh_a_hist_min_em
    frame = endpoint(
        symbol=ts_code.split(".")[0],
        period="1",
        start_date=f"{trade_date} 09:30:00",
        end_date=f"{trade_date} 15:00:00",
        adjust="",
    )
    if frame is None or frame.empty:
        raise RuntimeError(f"{trade_date} 无分钟行情")
    required = {"时间", "收盘"}
    if not required.issubset(frame.columns):
        raise RuntimeError(f"分钟行情字段缺失：{sorted(required - set(frame.columns))}")
    points = []
    for _, row in frame.iterrows():
        price = finite(row.get("收盘"))
        stamp = pd.to_datetime(row.get("时间"), errors="coerce")
        if price is not None and not pd.isna(stamp):
            points.append({"time": stamp.strftime("%H:%M"), "price": price})
    if not points:
        raise RuntimeError("分钟行情没有有效价格点")
    return points


def trigger_eastmoney_refresh(items: list[dict[str, Any]], trade_date: str, force: bool = False) -> None:
    """Refresh slower primary bars in the background without blocking the page."""

    global INTRADAY_REFRESHING, INTRADAY_LAST_STARTED, INTRADAY_PRIMARY_ERRORS
    now = time.time()
    with INTRADAY_PRIMARY_LOCK:
        if INTRADAY_REFRESHING or (not force and now - INTRADAY_LAST_STARTED < INTRADAY_TTL_SECONDS):
            return
        INTRADAY_REFRESHING = True
        INTRADAY_LAST_STARTED = now

    def worker() -> None:
        global INTRADAY_REFRESHING, INTRADAY_PRIMARY_ERRORS
        errors = []
        for item in items:
            try:
                points = eastmoney_intraday_points(item, trade_date)
                with INTRADAY_PRIMARY_LOCK:
                    INTRADAY_PRIMARY.setdefault(trade_date, {})[item["ts_code"]] = {
                        "points": points,
                        "source": "AkShare / 东方财富 · 1分钟不复权",
                    }
            except Exception as exc:
                errors.append(f"{item['ts_code']}：{exc}")
        with INTRADAY_PRIMARY_LOCK:
            INTRADAY_PRIMARY_ERRORS = errors
            INTRADAY_REFRESHING = False

    threading.Thread(target=worker, name="eastmoney-intraday-refresh", daemon=True).start()


def intraday_series(
    item: dict[str, Any],
    trade_date: str,
    fallback_points: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Return immediately from cached primary data or the single-request fallback."""

    ts_code = item["ts_code"]
    with INTRADAY_PRIMARY_LOCK:
        primary = INTRADAY_PRIMARY.get(trade_date, {}).get(ts_code)
    raw_points = list((primary or {}).get("points") or fallback_points or [])
    source = (primary or {}).get("source") or "Yahoo Finance · 1分钟快速回退"
    if not raw_points:
        raise RuntimeError(f"{trade_date} 无分钟行情")
    return {
        "ts_code": ts_code,
        "name": item.get("name") or ts_code,
        "points": raw_points,
        "latest_time": raw_points[-1]["time"],
        "source": source,
    }


def build_intraday_payload(force: bool = False) -> dict[str, Any]:
    started = time.perf_counter()
    trade_date = datetime.now().strftime("%Y-%m-%d")
    portfolio = portfolio_snapshot()
    holdings = portfolio["holdings"]
    items: dict[str, dict[str, Any]] = {}
    for holding in holdings:
        items[holding["ts_code"]] = holding
        for peer in holding["peers"]:
            items[peer["ts_code"]] = peer

    series: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    try:
        fallback = yahoo_intraday_fallback(list(items.values()), trade_date) if items else {}
    except Exception as exc:
        fallback = {}
        errors.append(f"Yahoo批量回退：{exc}")
    if items:
        trigger_eastmoney_refresh(list(items.values()), trade_date, force=force)
    for ts_code, item in items.items():
        try:
            series[ts_code] = intraday_series(item, trade_date, fallback.get(ts_code))
        except Exception as exc:
            errors.append(f"{ts_code}：{exc}")

    groups = []
    for holding in holdings:
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
            "source": "东方财富后台补齐 · Yahoo Finance快速回退 · 相对昨收由看板快照计算",
            "partial": bool(errors),
            "errors": errors,
            "fallback_count": sum(row.get("source", "").startswith("Yahoo") for row in series.values()),
            "primary_refreshing": INTRADAY_REFRESHING,
            "primary_errors": list(INTRADAY_PRIMARY_ERRORS),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "portfolio_version": portfolio["meta"]["portfolio_version"],
        },
        "groups": groups,
    }


def us_market(errors: list[str], holdings: list[dict[str, Any]]) -> dict[str, Any]:
    tickers = list(dict.fromkeys(
        row["ticker"] for holding in holdings for row in holding.get("us_map", [])
    ))

    def load() -> dict[str, Any]:
        if not tickers:
            return {}
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

    mapped = []
    for holding in holdings:
        peers = [{**item, **raw.get(item["ticker"], {})} for item in holding.get("us_map", [])]
        if peers:
            mapped.append({"holding": holding["name"], "peers": peers, "status": "候选映射 · 待确认"})
    dates = [row.get("date") for group in mapped for row in group["peers"] if row.get("date")]
    market_date = max(dates) if dates else None
    return {
        "market_date": market_date,
        "source": "Yahoo Finance · 最近完整交易日",
        "mapped_peers": mapped,
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
    breaches = []
    for group in groups:
        holding = group["holding"]
        loss = finite(holding.get("pnl_pct"))
        hard_stop = finite(holding.get("hard_stop_pct")) or finite(CONFIG.get("default_hard_stop_pct", 0.08))
        if loss is not None and hard_stop is not None and loss <= -hard_stop:
            breaches.append((holding, loss, hard_stop))
    level = "red"
    if breaches:
        holding, loss, hard_stop = min(breaches, key=lambda item: item[1])
        reason = (
            f"{holding['name']}{holding['shares_display']}由Excel同步，当前相对{holding['avg_cost']:.3f}元成本约{loss * 100:.1f}%，"
            f"已低于-{hard_stop * 100:.0f}%规则线；恢复期规则同时生效。"
        )
    elif groups:
        reason = f"交易记录.xlsx 已同步{len(groups)}个持仓；恢复期前5个交易日仍在执行。"
    else:
        reason = "交易记录.xlsx 当前无持仓；恢复期规则仍在执行。"
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
    portfolio = portfolio_snapshot(errors)
    holdings = portfolio["holdings"]
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="sections") as executor:
        groups_future = executor.submit(build_groups, timeframe, errors, holdings)
        us_future = executor.submit(us_market, errors, holdings)
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
            "portfolio_version": portfolio["meta"]["portfolio_version"],
            "ledger": portfolio["meta"],
        },
        "permission": permission_card(errors, groups),
        "holdings_status": {
            "confirmed": " · ".join(f"{item['name']}{item['shares_display']}" for item in holdings) if holdings else "当前无持仓",
            "unresolved": "；".join(portfolio["meta"].get("warnings", [])) or None,
            "portfolio_pnl_enabled": True,
            "positions": position_items,
            "sync": portfolio["meta"],
        },
        "peer_groups": groups,
        "us": us,
        "money_flow": flows,
    }


def build_peer_payload(timeframe: str) -> dict[str, Any]:
    if timeframe not in PERIOD_DAYS:
        timeframe = "1d"
    started = time.perf_counter()
    errors: list[str] = []
    get_pro()
    portfolio = portfolio_snapshot(errors)
    groups = build_groups(timeframe, errors, portfolio["holdings"])
    return {
        "meta": {
            "generated_at": iso_now(),
            "timeframe": timeframe,
            "partial": bool(errors),
            "errors": errors,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "portfolio_version": portfolio["meta"]["portfolio_version"],
        },
        "peer_groups": groups,
    }


def build_portfolio_payload() -> dict[str, Any]:
    errors: list[str] = []
    portfolio = portfolio_snapshot(errors)
    return {
        "meta": {**portfolio["meta"], "errors": errors, "partial": bool(errors)},
        "holdings": portfolio["holdings"],
    }


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(HERE), **kwargs)

    def end_headers(self) -> None:
        """Keep the local dashboard shell and assets from surviving a code update."""
        path = urlparse(self.path).path
        if path in {"/", "/index.html", "/app.js", "/styles.css"}:
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        super().end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/risk-model":
            try:
                self.send_json(read_risk_model())
            except Exception as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/reports":
            try:
                self.send_json(list_daily_reports())
            except Exception as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/report":
            query = parse_qs(parsed.query)
            kind = query.get("kind", [""])[0]
            date = query.get("date", [""])[0]
            try:
                self.send_json(get_daily_report(kind, date))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except Exception as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/journals":
            try:
                self.send_json(list_journals())
            except Exception as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/journal":
            date = parse_qs(parsed.query).get("date", [""])[0]
            try:
                self.send_json(get_journal(date))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except Exception as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/portfolio":
            try:
                self.send_json(build_portfolio_payload())
            except Exception as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
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
                    payload = build_intraday_payload(force=force)
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

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/api/journal":
            self.send_json({"error": "接口不存在"}, HTTPStatus.NOT_FOUND)
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json({"error": "Content-Length 无效"}, HTTPStatus.BAD_REQUEST)
            return
        if content_length <= 0:
            self.send_json({"error": "请求内容为空"}, HTTPStatus.BAD_REQUEST)
            return
        if content_length > JOURNAL_MAX_BYTES + 16_384:
            self.send_json({"error": "日记内容不能超过 500KB"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        try:
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("请求格式无效")
            result = save_journal(payload.get("date"), payload.get("content"))
            status = HTTPStatus.CREATED if result["created"] else HTTPStatus.OK
            self.send_json(result, status)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_json({"error": "请求不是有效的 UTF-8 JSON"}, HTTPStatus.BAD_REQUEST)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

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
