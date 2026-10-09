# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["pypdf==6.19.0", "PyYAML==6.0.3"]
# ///
"""核对器回归测试：篡改曾漏检的字段必须被拒绝。

运行：uv run scripts/test_course_path_verification.py
"""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import verify_course_path_expectations as verifier
import yaml


class VerificationTests(unittest.TestCase):
    def check(self, payload, lines, verify):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.yaml"
            path.write_text(
                yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8"
            )
            with patch.object(verifier, "page_lines", return_value=lines):
                return verify(path)[0]

    def test_plan_fields_and_group_requirements(self):
        payload = {
            "_meta": {"source": "fixture.pdf"},
            "courses": [
                {
                    "code": "CS1",
                    "name": "算法",
                    "kind": "必修",
                    "hours": "60",
                    "credits": 3,
                    "unit": "计算机系",
                    "grade": 2,
                    "semester": "秋",
                    "page": 1,
                }
            ],
            "groups": [
                {
                    "group": "核心课程",
                    "required_credits": "3",
                    "required_count": "1",
                    "page": 1,
                }
            ],
        }
        lines = {
            1: ["CS1 算法 必修 60 3 计算机系 2 秋", "核心课程要求学分:3 要求门数:1"]
        }
        self.assertFalse(self.check(payload, lines, verifier.verify_plan))
        for section, field, value in [
            ("courses", "hours", "40"),
            ("courses", "grade", 3),
            ("courses", "semester", "春"),
            ("groups", "required_credits", "4"),
            ("groups", "required_count", "2"),
        ]:
            with self.subTest(field=field):
                bad = copy.deepcopy(payload)
                bad[section][0][field] = value
                self.assertTrue(self.check(bad, lines, verifier.verify_plan))

    def test_relation_fields_and_original_page(self):
        payload = {
            "_meta": {"source": "fixture.pdf"},
            "relations": [
                {
                    "seq": 165,
                    "course_code": "CS1",
                    "course_name": "算法",
                    "unit": "计算机系",
                    "prereq_name": "图论",
                    "prereq_code": "M1",
                    "prereq_last_semester": None,
                    "prereq_status": "范围内",
                    "slice_page": 1,
                    "original_page": 14,
                }
            ],
        }
        lines = {1: ["165 CS1 算法 计算机系 图论 M1 范围内"]}
        self.assertFalse(self.check(payload, lines, verifier.verify_relations))
        for field, value in [
            ("course_name", "数据库"),
            ("unit", "物理系"),
            ("prereq_last_semester", "2020年春季学期"),
            ("original_page", 15),
        ]:
            with self.subTest(field=field):
                bad = copy.deepcopy(payload)
                bad["relations"][0][field] = value
                self.assertTrue(self.check(bad, lines, verifier.verify_relations))

    def test_substitution_fields_cannot_match_another_group(self):
        payload = {
            "_meta": {"source": "fixture.pdf"},
            "substitutions": [
                {
                    "seq": 14,
                    "group_id": "2775",
                    "substitute": {
                        "names": ["图形学"],
                        "codes": ["A"],
                        "credits": [3.0],
                        "raw": "A · 3 学分",
                    },
                    "original": {
                        "names": ["图形理论"],
                        "codes": ["CS1"],
                        "credits": [3.5],
                        "raw": "CS1 · 3.5 学分",
                    },
                    "direction": "单向（左→右）",
                    "is_course_group": False,
                    "slice_page": 1,
                    "original_page": 2,
                }
            ],
        }
        lines = {
            1: [
                "14 #2775 图形学",
                "A · 3 学分",
                "图形理论",
                "CS1 · 3.5 学分",
                "→ 单向",
                "15 #2776 另一组",
                "↔ 可相互替代 课程组",
            ]
        }
        self.assertFalse(self.check(payload, lines, verifier.verify_substitutions))
        for field, value in [
            ("direction", "可相互替代"),
            ("is_course_group", True),
            ("original_page", 3),
        ]:
            bad = copy.deepcopy(payload)
            bad["substitutions"][0][field] = value
            self.assertTrue(self.check(bad, lines, verifier.verify_substitutions))
        for field, value in [
            ("names", ["错误名称"]),
            ("codes", ["WRONG"]),
            ("credits", [4.0]),
        ]:
            bad = copy.deepcopy(payload)
            bad["substitutions"][0]["substitute"][field] = value
            self.assertTrue(self.check(bad, lines, verifier.verify_substitutions))


if __name__ == "__main__":
    unittest.main()
