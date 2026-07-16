import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from portfolio_sync import LedgerPortfolio, normalize_code, replay_positions, semantic_peer_rank, shared_business_phrases  # noqa: E402


class PortfolioSyncTests(unittest.TestCase):
    def test_code_normalization_preserves_leading_zero_and_alias(self):
        self.assertEqual(normalize_code("'002281"), "002281.SZ")
        self.assertEqual(normalize_code(600584), "600584.SH")
        self.assertEqual(normalize_code(2208), "002028.SZ")

    def test_replay_switches_position_from_old_to_new_symbol(self):
        seed = [{
            "ts_code": "600584.SH", "name": "长电科技", "shares": 100,
            "avg_cost": 100.0, "total_cost": 10000.0, "hard_stop_pct": 0.08,
        }]
        rows = pd.DataFrame([
            {"证券代码": 600584, "证券名称": "长电科技", "买卖标志": "卖出", "成交数量": -100, "成交价格": 90, "成交金额": 9000, "剩余仓位": 0},
            {"证券代码": "002028", "证券名称": "思源电气", "买卖标志": "买入", "成交数量": 200, "成交价格": 80, "成交金额": 16000, "剩余仓位": 200},
        ])
        positions, warnings = replay_positions(seed, rows)
        self.assertEqual(warnings, [])
        self.assertEqual([item["ts_code"] for item in positions], ["002028.SZ"])
        self.assertEqual(positions[0]["shares"], 200)
        self.assertEqual(positions[0]["avg_cost"], 80)

    def test_semantic_model_prefers_direct_business_peer(self):
        target = {"ts_code": "600584.SH", "name": "长电科技", "industry": "半导体", "main_business": "集成电路、分立器件的封装与测试，提供半导体封装测试解决方案"}
        candidates = [
            {"ts_code": "002185.SZ", "name": "华天科技", "industry": "半导体", "main_business": "集成电路的封装与测试业务"},
            {"ts_code": "300613.SZ", "name": "富瀚微", "industry": "半导体", "main_business": "集成电路设计"},
            {"ts_code": "688362.SH", "name": "甬矽电子", "industry": "半导体", "main_business": "集成电路的封装和测试"},
        ]
        peers = semantic_peer_rank(target, candidates, 2)
        self.assertEqual({peer["ts_code"] for peer in peers}, {"002185.SZ", "688362.SH"})
        self.assertTrue(all(peer["ai_reason"].startswith("共同业务语义：") for peer in peers))
        self.assertIn("封装与测试", shared_business_phrases(target["main_business"], candidates[0]["main_business"]))

    def test_real_ledger_starts_from_confirmed_checkpoint(self):
        settings = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
        portfolio = LedgerPortfolio(
            (HERE / settings["ledger_path"]).resolve(),
            (HERE / settings["position_seed"]).resolve(),
            settings,
        ).load()
        shares = {item["ts_code"]: item["shares"] for item in portfolio["holdings"]}
        self.assertEqual(shares, {"588060.SH": 100, "600584.SH": 900})
        self.assertEqual(portfolio["meta"]["replayed_rows"], 0)
        self.assertFalse(portfolio["meta"]["history_changed"])

    def test_file_change_rebuilds_portfolio_without_restarting_engine(self):
        first = pd.DataFrame([{
            "证券代码": 600584, "证券名称": "长电科技", "买卖标志": "买入", "成交日期": 20260717,
            "成交价格": 90, "成交数量": 100, "成交金额": 9000, "剩余仓位": 100,
        }])
        second = pd.DataFrame([
            *first.to_dict("records"),
            {"证券代码": 600584, "证券名称": "长电科技", "买卖标志": "卖出", "成交日期": 20260718, "成交价格": 88, "成交数量": -100, "成交金额": 8800, "剩余仓位": 0},
            {"证券代码": "002028", "证券名称": "思源电气", "买卖标志": "买入", "成交日期": 20260718, "成交价格": 80, "成交数量": 200, "成交金额": 16000, "剩余仓位": 200},
        ])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger_path = root / "ledger.xlsx"
            seed_path = root / "seed.json"
            ledger_path.write_text("v1", encoding="utf-8")
            seed_path.write_text(json.dumps({"sheet": "交易记录", "checkpoint_data_rows": 0, "positions": []}), encoding="utf-8")
            engine = LedgerPortfolio(ledger_path, seed_path, {"default_hard_stop_pct": 0.08})
            with patch("portfolio_sync.pd.read_excel", return_value=first):
                before = engine.load()
            ledger_path.write_text("version-two", encoding="utf-8")
            with patch("portfolio_sync.pd.read_excel", return_value=second):
                after = engine.load()
            self.assertEqual([item["ts_code"] for item in before["holdings"]], ["600584.SH"])
            self.assertEqual([item["ts_code"] for item in after["holdings"]], ["002028.SZ"])
            self.assertNotEqual(before["meta"]["portfolio_version"], after["meta"]["portfolio_version"])


if __name__ == "__main__":
    unittest.main()
