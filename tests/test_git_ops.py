import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_farm.git_ops import collect_changed_files, collect_patch


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


class GitOpsTests(unittest.TestCase):
    def test_collect_patch_includes_untracked_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git(repo, "init")
            (repo / "README.md").write_text("base\n", encoding="utf-8")
            git(repo, "add", "README.md")
            git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-m", "init")

            (repo / "web").mkdir()
            (repo / "web" / "page.html").write_text("<h1>Hello</h1>\n", encoding="utf-8")

            changed = collect_changed_files(repo)
            patch = collect_patch(repo)

        self.assertEqual(changed[0].status, "A")
        self.assertEqual(changed[0].path, "web/page.html")
        self.assertIn("new file mode", patch)
        self.assertIn("<h1>Hello</h1>", patch)


if __name__ == "__main__":
    unittest.main()
