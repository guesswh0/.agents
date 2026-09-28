from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.home = self.root / "home with spaces"
        self.home.mkdir()
        self.repo = self.root / "agent source"
        self.repo.mkdir()
        shutil.copy2(ROOT / "setup.sh", self.repo / "setup.sh")
        shutil.copy2(ROOT / ".gitignore", self.repo / ".gitignore")
        (self.repo / "AGENTS.md").write_text("Shared instructions\n")
        self.git("init", "-q")
        self.git("add", "AGENTS.md", "setup.sh", ".gitignore")

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    def skill(self, name, tracked=True):
        path = self.repo / "skills" / name
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
        if tracked:
            self.git("add", "-f", str(path))
        return path

    def install(self, check=True):
        return subprocess.run(
            ["bash", str(self.repo / "setup.sh"), str(self.home)],
            check=check,
            capture_output=True,
            text=True,
        )

    def test_installs_owned_skills_and_preserves_external_collection(self):
        portable = self.skill("portable")
        adapter = self.skill("claude-agent")
        self.skill("third-party", tracked=False)
        external = self.root / "external"
        external.mkdir()
        (external / "SKILL.md").write_text("External skill\n")
        skills = self.home / ".agents" / "skills"
        skills.mkdir(parents=True)
        (skills / "external").symlink_to(external)

        self.install()

        self.assertEqual((skills / "portable").resolve(), portable)
        self.assertEqual((skills / "claude-agent").resolve(), adapter)
        self.assertEqual((skills / "external").readlink(), external)
        self.assertEqual((external / "SKILL.md").read_text(), "External skill\n")
        self.assertFalse((skills / "third-party").exists())
        self.assertEqual(
            (self.home / ".claude/skills/portable").resolve(), portable
        )
        self.assertFalse((self.home / ".claude/skills/claude-agent").exists())
        self.assertFalse((self.home / ".codex/skills").exists())
        for relative in (".codex/AGENTS.md", ".claude/CLAUDE.md"):
            self.assertEqual(
                (self.home / relative).resolve(), self.repo / "AGENTS.md"
            )

    def test_existing_files_directories_and_broken_links_are_backed_up_once(self):
        self.skill("portable")
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
        self.install()

        backups = list(codex.parent.glob("AGENTS.md.backup.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "Previous instructions\n")
        backups = list(claude.parent.glob("CLAUDE.md.backup.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].readlink(), self.root / "missing")
        backups = list(skill.parent.glob("portable.backup.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "custom.txt").read_text(), "Keep this\n")

    def test_in_place_install_keeps_real_skill_directories(self):
        self.skill("portable")
        destination = self.home / ".agents"
        shutil.move(self.repo, destination)
        self.repo = destination

        self.install()
        self.install()

        skill = self.repo / "skills/portable"
        self.assertTrue(skill.is_dir())
        self.assertFalse(skill.is_symlink())
        self.assertEqual(list(skill.parent.glob("*.backup.*")), [])

    def test_no_skills_still_installs_instructions(self):
        self.install()
        self.assertEqual(
            (self.home / ".codex/AGENTS.md").read_text(), "Shared instructions\n"
        )

    def test_foreign_skills_and_lock_files_remain_outside_git(self):
        self.skill("third-party", tracked=False)
        (self.repo / ".skill-lock.json").write_text("{}")
        (self.repo / ".skill-lock.json.bak-old").write_text("{}")
        self.git("add", "--all")
        tracked = self.git("ls-files").stdout.splitlines()
        self.assertEqual(tracked, [".gitignore", "AGENTS.md", "setup.sh"])

    def test_missing_repository_fails_before_installing_anything(self):
        shutil.rmtree(self.repo / ".git")
        self.assertNotEqual(self.install(check=False).returncode, 0)
        self.assertEqual(list(self.home.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
