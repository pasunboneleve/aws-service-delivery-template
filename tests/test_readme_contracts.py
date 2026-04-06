import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
README_PATH = ROOT_DIR / "README.md"
UPDATE_SCRIPT = ROOT_DIR / "scripts" / "update-readme-live-url.sh"
START_MARKER = "<!-- LIVE_URL_START -->"
END_MARKER = "<!-- LIVE_URL_END -->"


class ReadmeContractsTest(unittest.TestCase):
    def test_readme_contains_one_live_url_marker_pair(self) -> None:
        content = README_PATH.read_text(encoding="utf-8")
        self.assertEqual(content.count(START_MARKER), 1)
        self.assertEqual(content.count(END_MARKER), 1)

    def test_updater_only_changes_content_inside_marker_block(self) -> None:
        original, updated = self._run_updater("https://example.com/live")
        original_prefix, original_body, original_suffix = self._split_readme(original)
        updated_prefix, updated_body, updated_suffix = self._split_readme(updated)

        self.assertEqual(original_prefix, updated_prefix)
        self.assertEqual(original_suffix, updated_suffix)
        self.assertNotEqual(original_body, updated_body)
        self.assertIn("- Service URL: [Live URL](https://example.com/live)", updated_body)

    def test_empty_output_becomes_todo_placeholder(self) -> None:
        _, updated = self._run_updater("")
        _, updated_body, _ = self._split_readme(updated)
        self.assertIn("- Service URL: `TODO`", updated_body)

    def _run_updater(self, live_url: str) -> tuple[str, str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir) / "repo"
            shutil.copytree(ROOT_DIR, temp_root)

            stub_dir = Path(temp_dir) / "bin"
            stub_dir.mkdir()

            tofu_stub = stub_dir / "tofu"
            tofu_stub.write_text(
                "#!/usr/bin/env bash\n"
                "if [ \"$1\" = \"output\" ] && [ \"$2\" = \"-raw\" ] && [ \"$3\" = \"service_url\" ]; then\n"
                f"  printf '%s' '{live_url}'\n"
                "  exit 0\n"
                "fi\n"
                "echo 'unexpected tofu invocation' >&2\n"
                "exit 1\n",
                encoding="utf-8",
            )
            tofu_stub.chmod(0o755)

            original = (temp_root / "README.md").read_text(encoding="utf-8")
            env = os.environ.copy()
            env["PATH"] = f"{stub_dir}:{env['PATH']}"
            subprocess.run(
                [str(temp_root / "scripts" / "update-readme-live-url.sh")],
                cwd=temp_root,
                check=True,
                env=env,
            )
            updated = (temp_root / "README.md").read_text(encoding="utf-8")
            return original, updated

    def _split_readme(self, content: str) -> tuple[str, str, str]:
        start_index = content.index(START_MARKER)
        end_index = content.index(END_MARKER)
        end_marker_end = end_index + len(END_MARKER)
        return (
            content[:start_index],
            content[start_index:end_marker_end],
            content[end_marker_end:],
        )


if __name__ == "__main__":
    unittest.main()
