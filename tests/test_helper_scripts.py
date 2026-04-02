import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT_DIR / "scripts" / "check-ecr-image.sh"


class HelperScriptContractsTest(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("jq") is None:
            self.skipTest("jq is required to exercise check-ecr-image.sh")

    def test_existing_image_returns_true(self) -> None:
        result = self._run_script(
            aws_script=(
                "#!/usr/bin/env bash\n"
                "cat <<'EOF'\n"
                '{"imageDetails":[{"imageDigest":"sha256:test"}]}\n'
                "EOF\n"
            )
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {"exists": "true"})

    def test_missing_image_returns_false(self) -> None:
        result = self._run_script(
            aws_script=(
                "#!/usr/bin/env bash\n"
                "echo 'An error occurred (ImageNotFoundException) when calling the DescribeImages operation' >&2\n"
                "exit 255\n"
            )
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {"exists": "false"})

    def test_auth_or_api_failure_exits_non_zero(self) -> None:
        result = self._run_script(
            aws_script=(
                "#!/usr/bin/env bash\n"
                "echo 'An error occurred (AccessDeniedException) when calling the DescribeImages operation' >&2\n"
                "exit 255\n"
            )
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ECR check failed:", result.stderr)

    def _run_script(self, aws_script: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            stub_dir = Path(temp_dir) / "bin"
            stub_dir.mkdir()

            aws_stub = stub_dir / "aws"
            aws_stub.write_text(aws_script, encoding="utf-8")
            aws_stub.chmod(0o755)

            env = os.environ.copy()
            env["PATH"] = f"{stub_dir}:{env['PATH']}"
            payload = json.dumps(
                {
                    "repository_url": "123456789012.dkr.ecr.ap-southeast-2.amazonaws.com/example",
                    "image_tag": "latest",
                    "aws_region": "ap-southeast-2",
                }
            )

            return subprocess.run(
                ["bash", str(SCRIPT_PATH)],
                input=payload,
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )


if __name__ == "__main__":
    unittest.main()
