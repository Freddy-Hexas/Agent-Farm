import unittest

from agent_farm.models import AgentFarmConfig, ChangedFile, TestResult
from agent_farm.review import count_diff_lines, path_matches, run_machine_review


class PathMatchingTests(unittest.TestCase):
    def test_prefix_pattern_matches_subtree(self):
        self.assertTrue(path_matches("src/auth", "src/auth/login.py"))
        self.assertFalse(path_matches("src/auth", "src/payments/login.py"))

    def test_glob_pattern_matches(self):
        self.assertTrue(path_matches("**/.env.*", "apps/web/.env.local"))
        self.assertFalse(path_matches("**/.env.*", "apps/web/env.local"))

    def test_sensitive_glob_matches_root_and_is_case_insensitive(self):
        self.assertTrue(path_matches("**/*secret*", "secrets.txt"))
        self.assertTrue(path_matches("**/*secret*", "SECRET.txt"))


class MachineReviewTests(unittest.TestCase):
    def test_rejects_forbidden_path(self):
        config = AgentFarmConfig(
            allowed_paths=[],
            forbidden_paths=[".env", "**/.env.*"],
            test_commands=[],
        )
        review = run_machine_review(
            config,
            [ChangedFile(status="M", path=".env")],
            "diff --git a/.env b/.env\n+SECRET=1\n",
            [],
        )
        self.assertEqual(review.status, "failed")
        self.assertTrue(any(f.code == "forbidden_path" for f in review.findings))

    def test_warns_without_tests_but_passes_when_no_errors(self):
        config = AgentFarmConfig(
            allowed_paths=["src/auth"],
            forbidden_paths=[],
            test_commands=[],
        )
        review = run_machine_review(
            config,
            [ChangedFile(status="M", path="src/auth/login.py")],
            "diff --git a/src/auth/login.py b/src/auth/login.py\n+pass\n",
            [],
        )
        self.assertEqual(review.status, "passed")
        self.assertTrue(any(f.code == "no_orchestrator_tests" for f in review.findings))

    def test_failed_test_rejects(self):
        config = AgentFarmConfig(test_commands=["python -m unittest"])
        tests = [
            TestResult(
                command="python -m unittest",
                returncode=1,
                log_file="tests/01.log",
                duration_seconds=0.1,
            )
        ]
        review = run_machine_review(
            config,
            [ChangedFile(status="M", path="src/app.py")],
            "diff --git a/src/app.py b/src/app.py\n+pass\n",
            tests,
        )
        self.assertEqual(review.status, "failed")
        self.assertTrue(any(f.code == "test_failed" for f in review.findings))

    def test_rename_checks_old_path_against_forbidden_patterns(self):
        config = AgentFarmConfig(forbidden_paths=["**/*secret*"], test_commands=[])
        review = run_machine_review(
            config,
            [ChangedFile(status="R100", old_path="config/secrets.json", path="config/public.json")],
            "diff --git a/config/secrets.json b/config/public.json\n",
            [],
        )
        self.assertEqual(review.status, "failed")
        self.assertTrue(
            any(f.code == "forbidden_path" and f.path == "config/secrets.json" for f in review.findings)
        )

    def test_diff_line_count_ignores_headers(self):
        patch = "\n".join(
            [
                "diff --git a/a.py b/a.py",
                "--- a/a.py",
                "+++ b/a.py",
                "-old",
                "+new",
            ]
        )
        self.assertEqual(count_diff_lines(patch), 2)


if __name__ == "__main__":
    unittest.main()
