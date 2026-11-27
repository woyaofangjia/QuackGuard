import unittest

from quackguard.inference import apply_decision_patch
from quackguard.iteration import _directive_patch


class IterationPatchTest(unittest.TestCase):
    def test_parses_explicit_label_and_applicability(self):
        patch = _directive_patch("依据 R014@3.0 原文，该单元应判为 ambiguous，适用性 applicable。")
        self.assertEqual(patch["label"], "ambiguous")
        self.assertEqual(patch["applicability"], "applicable")

    def test_unknown_applicability_implies_ambiguous(self):
        patch = _directive_patch("该单元适用性 unknown。")
        self.assertEqual(patch["label"], "ambiguous")
        self.assertEqual(patch["applicability"], "unknown")

    def test_confirmation_is_not_a_patch(self):
        self.assertIsNone(_directive_patch("确认按原文执行，不改变判定。"))

    def test_patch_applies_only_to_completed_output(self):
        task = {
            "audit_unit_id": "u1",
            "rule_id": "R007",
            "rule_version": "3.0",
            "metadata": {},
            "materials": [{"material_id": "m1", "page_index": 1}],
        }
        row = {
            "audit_unit_id": "u1",
            "rule_id": "R007",
            "rule_version": "3.0",
            "artifact_version": "av_test",
            "trace_id": "t1",
            "execution_status": "completed",
            "label": "pass",
            "applicability": "applicable",
            "reason_code": "model_review",
            "reason": "原判定",
            "evidence": [{"material_id": "m1", "page_index": 1, "fact": "可见"}],
            "latency_ms": 1.0,
            "cost_units": None,
        }
        patch = {"label": "reject", "reason": "教师更正", "reason_code": "teacher_feedback"}
        self.assertTrue(apply_decision_patch(row, patch, task, "av_test"))
        self.assertEqual(row["label"], "reject")
        self.assertEqual(row["reason"], "教师更正")

        broken = {"label": "bogus"}
        self.assertFalse(apply_decision_patch(row, broken, task, "av_test"))
        self.assertEqual(row["label"], "reject")


if __name__ == "__main__":
    unittest.main()
