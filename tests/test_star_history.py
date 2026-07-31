import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "update_star_history.py"
SPEC = importlib.util.spec_from_file_location("update_star_history", SCRIPT)
assert SPEC and SPEC.loader
star_history = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(star_history)


class StarHistoryTests(unittest.TestCase):
    def test_record_sample_sorts_dates_and_replaces_same_day(self):
        history = {
            "schema_version": 1,
            "repository": "owner/repo",
            "tracking_started": "2026-07-30",
            "samples": [{"date": "2026-07-31", "stars": 2}],
        }

        star_history.record_sample(history, "2026-07-30", 1)
        star_history.record_sample(history, "2026-07-31", 3)

        self.assertEqual(
            history["samples"],
            [
                {"date": "2026-07-30", "stars": 1},
                {"date": "2026-07-31", "stars": 3},
            ],
        )

    def test_render_svg_contains_accessible_aggregate_chart(self):
        svg = star_history.render_svg(
            {
                "schema_version": 1,
                "repository": "owner/repo",
                "tracking_started": "2026-07-30",
                "samples": [
                    {"date": "2026-07-30", "stars": 1},
                    {"date": "2026-07-31", "stars": 4},
                ],
            }
        )

        self.assertIn("<svg", svg)
        self.assertIn("owner/repo GitHub star history", svg)
        self.assertIn("CURRENT STARS", svg)
        self.assertIn("★ 4", svg)
        self.assertIn("no Stargazer identities are collected", svg)

    def test_cli_can_generate_files_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "history.json"
            svg = root / "history.svg"

            result = star_history.main(
                [
                    "--repository",
                    "owner/repo",
                    "--date",
                    "2026-07-31",
                    "--stars",
                    "7",
                    "--data",
                    str(data),
                    "--output",
                    str(svg),
                ]
            )

            self.assertEqual(result, 0)
            self.assertIn('"stars": 7', data.read_text(encoding="utf-8"))
            self.assertIn("★ 7", svg.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
