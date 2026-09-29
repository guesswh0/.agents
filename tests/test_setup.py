from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent


class SetupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / "home with spaces"
        self.home.mkdir()
        self.repo = self.root / "agent source"
        self.repo.mkdir()
        shutil.copy2(ROOT / "setup.sh", self.repo / "setup.sh")
        (self.repo / "AGENTS.md").write_text("Shared instructions\n")
        skill = self.repo / "local/skills/portable"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("Local skill\n")
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)

    def install(self):
        subprocess.run(
            ["bash", str(self.repo / "setup.sh"), str(self.home)],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_conflicting_files_directories_and_broken_links_are_preserved(self):
        codex = self.home / ".codex/AGENTS.md"
        codex.parent.mkdir()
        codex.write_text("Previous instructions\n")
        claude = self.home / ".claude/CLAUDE.md"
        claude.parent.mkdir()
        claude.symlink_to(self.root / "missing")
        skill = self.home / ".claude/skills/portable"
        skill.mkdir(parents=True)
        (skill / "custom.txt").write_text("Keep this\n")

        self.install()

        backups = self.home / ".agents/backups"
        self.assertEqual(len(list(backups.iterdir())), 3)
        self.assertEqual(
            next(backups.glob("AGENTS.md.*/original")).read_text(),
            "Previous instructions\n",
        )
        self.assertEqual(
            next(backups.glob("CLAUDE.md.*/original")).readlink(), self.root / "missing"
        )
        self.assertEqual(
            next(backups.glob("portable.*/original/custom.txt")).read_text(),
            "Keep this\n",
        )
        self.assertEqual(list(skill.parent.iterdir()), [skill])

    def test_repeated_setup_leaves_links_unchanged(self):
        self.install()
        links = [
            self.home / name
            for name in (
                ".codex/AGENTS.md",
                ".claude/CLAUDE.md",
                ".agents/skills/portable",
                ".claude/skills/portable",
            )
        ]
        before = [(path.readlink(), path.lstat().st_ino) for path in links]

        self.install()

        self.assertEqual(
            [(path.readlink(), path.lstat().st_ino) for path in links], before
        )
        self.assertFalse((self.home / ".agents/backups").exists())


if __name__ == "__main__":
    unittest.main()
