import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_farm.git_ops import collect_changed_files, collect_patch, create_workspace_snapshot, resolve_ref


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


class GitOpsTests(unittest.TestCase):
    def test_workspace_snapshot_includes_uncommitted_files_without_touching_user_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git(repo, "init")
            (repo / "README.md").write_text("base\n", encoding="utf-8")
            git(repo, "add", "README.md")
            git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-m", "init")
            base_commit = resolve_ref(repo, "HEAD")

            (repo / "README.md").write_text("workspace\n", encoding="utf-8")
            (repo / "agent_farm").mkdir()
            (repo / "agent_farm" / "task_runtime.py").write_text("runtime\n", encoding="utf-8")
            (repo / "secret.txt").write_text("do not snapshot\n", encoding="utf-8")

            snapshot = create_workspace_snapshot(
                repo,
                base_commit,
                forbidden_paths=["**/*secret*"],
            )

            self.assertNotEqual(snapshot, base_commit)
            shown_readme = subprocess.run(
                ["git", "show", f"{snapshot}:README.md"],
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
            )
            shown_runtime = subprocess.run(
                ["git", "show", f"{snapshot}:agent_farm/task_runtime.py"],
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
            )
            missing_secret = subprocess.run(
                ["git", "cat-file", "-e", f"{snapshot}:secret.txt"],
                cwd=repo,
                capture_output=True,
                text=True,
            )

            self.assertEqual(shown_readme.stdout, "workspace\n")
            self.assertEqual(shown_runtime.stdout, "runtime\n")
            self.assertNotEqual(missing_secret.returncode, 0)
            self.assertEqual(
                subprocess.run(
                    ["git", "diff", "--cached", "--quiet"],
                    cwd=repo,
                ).returncode,
                0,
            )
            self.assertIn("?? agent_farm/", subprocess.run(
                ["git", "status", "--short"],
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
            ).stdout)

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

    def test_collect_patch_includes_explicitly_allowed_ignored_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git(repo, "init")
            (repo / ".gitignore").write_text("test-artifacts/\n", encoding="utf-8")
            git(repo, "add", ".gitignore")
            git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-m", "init")

            artifact = repo / "test-artifacts" / "proof.md"
            artifact.parent.mkdir()
            artifact.write_text("real artifact\n", encoding="utf-8")

            patch = collect_patch(repo, include_ignored_paths=["test-artifacts/proof.md"])

            self.assertIn("new file mode", patch)
            self.assertIn("test-artifacts/proof.md", patch)
            self.assertIn("real artifact", patch)


if __name__ == "__main__":
    unittest.main()
