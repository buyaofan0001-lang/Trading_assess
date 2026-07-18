import json
import tempfile
import unittest
from pathlib import Path

from server import read_risk_model, risk_feature_label


class RiskModelReaderTests(unittest.TestCase):
    def test_reads_official_compact_outputs_and_preserves_safety_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "results").mkdir()
            (root / "config").mkdir()
            (root / "results" / "current_warning_brief.json").write_text(json.dumps({
                "signal_date": "2026-07-16",
                "model_version": "v8-test",
                "model_specification_sha256": "abc",
                "display_state": "diagnostic_only_already_weak",
                "operational_new_warning": False,
                "warning_eligible": False,
                "raw_warning_tier": "normal",
                "raw_warning_score": 0.023,
                "calibrated_selloff_probability": 0.015,
                "automatic_position_action_available": False,
                "shadow_validation_status": "collecting",
                "data_freshness_status": "stale",
                "data_freshness_pass": False,
                "stale_data_symbols": ["USDCNH.FXCM"],
                "message": "只作诊断；低分不能解释为安全。",
                "top_risk_up_factors": [{
                    "feature": "tech_vol60", "value": 0.03,
                    "training_percentile": 0.95, "score_contribution": 0.005,
                }],
                "top_risk_down_factors": [],
            }), encoding="utf-8")
            (root / "config" / "frozen_model_manifest.json").write_text(json.dumps({
                "model_version": "v8-test",
                "model_specification_sha256": "abc",
                "model_specification": {"signal": {"warning_threshold": 0.55, "high_warning_threshold": 0.70}},
            }), encoding="utf-8")
            (root / "results" / "forward_shadow_summary.csv").write_text(
                "status,promotion_pass,matured_eligible_rows,minimum_rows_required,positive_events,minimum_positive_events_required\n"
                "collecting,False,3,252,1,25\n", encoding="utf-8",
            )

            payload = read_risk_model(root)
            self.assertTrue(payload["available"])
            self.assertEqual(payload["status"]["code"], "stale")
            self.assertFalse(payload["operational"]["automatic_position_action_available"])
            self.assertEqual(payload["model"]["deployment_status"], "research_only")
            self.assertEqual(payload["shadow"]["matured_rows"], 3)
            self.assertEqual(payload["factors"]["up"][0]["label"], "科技60日波动率")

    def test_missing_output_is_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            payload = read_risk_model(Path(temp))
        self.assertFalse(payload["available"])
        self.assertIn("缺少模型输出", payload["error"])

    def test_generic_feature_labels_remain_readable(self):
        self.assertEqual(risk_feature_label("tech_dd20"), "科技20日回撤")
        self.assertEqual(risk_feature_label("unknown_factor"), "unknown_factor")


if __name__ == "__main__":
    unittest.main()
