import unittest

from quackguard_ocr.grounding import _candidate_score, _normalized, _to_bbox


class OcrGroundingTest(unittest.TestCase):
    def test_value_match_beats_label_only(self):
        with_value = _candidate_score("型号 :EDU-753", "型号：EDU-753", "型号")
        label_only = _candidate_score("型号：", None, "型号")
        self.assertGreater(with_value, label_only)

    def test_label_only_rejects_other_model_line(self):
        self.assertEqual(_candidate_score("主型号：EDU-753", None, "型号"), 0)

    def test_normalizes_fullwidth_and_spaces(self):
        self.assertEqual(_normalized("型号 ：ＡＢＣ"), "型号:abc")

    def test_bbox_from_points(self):
        self.assertEqual(
            _to_bbox([[10, 20], [110, 20], [110, 60], [10, 60]], 200, 100),
            [0.05, 0.2, 0.55, 0.6],
        )


if __name__ == "__main__":
    unittest.main()
