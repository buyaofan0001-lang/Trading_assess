from pathlib import Path
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from server import (
    analyze_macro_series,
    build_macro_signal_summary,
    compute_volatility_metrics,
    get_daily_report,
    list_daily_reports,
    read_macro_framework,
)


class DailyReportTests(unittest.TestCase):
    def test_reads_versioned_macro_framework_without_modifying_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "分析框架.md"
            source = "## 宏观视角\n\n### 信用风险\n观察 OAS"
            path.write_text(source, encoding="utf-8")
            payload = read_macro_framework(path)
            self.assertTrue(payload["exists"])
            self.assertEqual(payload["content"], source)
            self.assertEqual(payload["title"], "宏观视角")
            self.assertEqual([item["title"] for item in payload["sections"]], ["宏观视角", "信用风险"])
            self.assertEqual(path.read_text(encoding="utf-8"), source)

    def test_macro_framework_version_changes_after_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "分析框架.md"
            path.write_text("## 宏观视角", encoding="utf-8")
            before = read_macro_framework(path)["version"]
            path.write_text("## 宏观视角\n\n### 市场流动性", encoding="utf-8")
            after = read_macro_framework(path)["version"]
            self.assertNotEqual(before, after)

    def test_macro_anomaly_uses_only_prior_observations(self):
        dates = pd.date_range("2026-01-01", periods=66, freq="D")
        ordinary_changes = [0.08, -0.06, 0.03, -0.02, 0.05] * 13
        levels = [300.0]
        for change in ordinary_changes[:-1]:
            levels.append(levels[-1] + change)
        levels.append(levels[-1] + 9.0)
        result = analyze_macro_series(pd.Series(levels, index=dates))
        anomaly = result["recent_anomaly"]
        self.assertIsNotNone(anomaly)
        self.assertEqual(anomaly["date"], dates[-1].strftime("%Y-%m-%d"))
        self.assertEqual(anomaly["window"], "1D")
        self.assertGreater(abs(anomaly["z"]), 20)

    def test_macro_summary_keeps_observation_separate_from_trade_permission(self):
        metrics = [
            {"id": "oas", "change_5d": 6.0},
            {"id": "jnk", "change_5d": -0.46},
            {"id": "ust10y", "change_5d": 14.0},
            {"id": "cn_repo_spread", "latest": 1.51},
            {"id": "usd_funding_spread", "latest": -1.0},
        ]
        summary = build_macro_signal_summary(metrics)
        self.assertIn("信用风险偏好转弱", summary["headline"])
        self.assertIn("长端利率上行", summary["headline"])
        self.assertIn("不直接生成买卖许可", summary["boundary"])

    def test_discovers_both_report_kinds_and_reads_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pre = root / "logs" / "pre-market" / "2026"
            close = root / "logs" / "2026"
            pre.mkdir(parents=True)
            close.mkdir(parents=True, exist_ok=True)
            (pre / "2026-07-16.md").write_text("# 盘前报告\n\n## 一句话结论\n风险偏高", encoding="utf-8")
            (close / "2026-07-16.md").write_text("# 收盘复盘\n\n## 市场总览\n科技承压", encoding="utf-8")

            library = list_daily_reports(root)
            self.assertEqual(library["reports"]["premarket"]["latest"], "2026-07-16")
            self.assertEqual(library["reports"]["close"]["latest"], "2026-07-16")
            report = get_daily_report("close", "2026-07-16", root)
            self.assertIn("市场总览", report["content"])

    def test_library_version_changes_when_new_report_arrives(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "logs" / "pre-market" / "2026"
            folder.mkdir(parents=True)
            (folder / "2026-07-15.md").write_text("# 7月15日", encoding="utf-8")
            before = list_daily_reports(root)["library_version"]
            (folder / "2026-07-16.md").write_text("# 7月16日", encoding="utf-8")
            after = list_daily_reports(root)["library_version"]
            self.assertNotEqual(before, after)

    def test_marks_due_weekday_report_as_missing_without_hiding_latest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "logs" / "pre-market" / "2026"
            folder.mkdir(parents=True)
            (folder / "2026-07-15.md").write_text("# 7月15日", encoding="utf-8")
            now = datetime(2026, 7, 17, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
            report = list_daily_reports(root, now=now)["reports"]["premarket"]
            self.assertEqual(report["latest"], "2026-07-15")
            self.assertTrue(report["health"]["stale"])
            self.assertIn("当前展示 2026-07-15", report["health"]["message"])

    def test_atr_boundary_uses_prior_sessions_and_flags_extreme_drop(self):
        rows = [{
            "trade_date": f"202607{day:02d}", "high": 101.0, "low": 99.0,
            "pre_close": 100.0, "close": 100.0,
        } for day in range(1, 17)]
        rows.append({
            "trade_date": "20260717", "high": 120.0, "low": 80.0,
            "pre_close": 100.0, "close": 90.0,
        })
        live = {
            "market_date": "20260717", "pre_close": 100.0, "price": 96.0,
            "low": 95.0, "return": -0.04,
        }
        metrics = compute_volatility_metrics(pd.DataFrame(rows), live)
        self.assertAlmostEqual(metrics["atr_value"], 2.0)
        self.assertAlmostEqual(metrics["current_down_atr"], 2.0)
        self.assertAlmostEqual(metrics["intraday_low_atr"], 2.5)
        self.assertEqual(metrics["status_code"], "extreme")
        self.assertEqual(metrics["history_end"], "20260716")


if __name__ == "__main__":
    unittest.main()
