from pathlib import Path
import tempfile
import unittest

from server import get_daily_report, list_daily_reports


class DailyReportTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
