import json
from pathlib import Path
import sys
import unittest

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


if __name__ == "__main__":
    unittest.main()
