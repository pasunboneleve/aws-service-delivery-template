import os
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT_DIR / "scripts" / "run-aws-integration.sh"


class IntegrationRunnerPreflightTest(unittest.TestCase):
    def test_preflight_derives_github_repo_from_origin(self) -> None:
        for origin_url in (
            "git@github.com:example-org/example-repo.git",
            "https://github.com/example-org/example-repo.git",
        ):
            with self.subTest(origin_url=origin_url):
                result = self._run_preflight(
                    extra_env={},
                    git_script=(
                        "#!/usr/bin/env bash\n"
                        f"printf '%s\\n' '{origin_url}'\n"
                    ),
                )

                self.assertIn(
                    "ready: GitHub repo for integration runs is example-repo (derived from git origin)",
                    result.stdout,
                )

    def test_preflight_prefers_explicit_github_repo_override(self) -> None:
        result = self._run_preflight(extra_env={"GITHUB_REPO": "override-repo"})

        self.assertIn(
            "ready: GitHub repo for integration runs is override-repo (via GITHUB_REPO)",
            result.stdout,
        )

    def test_preflight_reports_missing_repo_when_origin_cannot_be_derived(self) -> None:
        result = self._run_preflight(
            extra_env={},
            git_script=(
                "#!/usr/bin/env bash\n"
                "exit 1\n"
            ),
        )

        self.assertIn(
            "missing: GitHub repo for integration runs is not configured",
            result.stdout,
        )

    def test_preflight_handles_missing_git_binary(self) -> None:
        result = self._run_mode("preflight", extra_env={}, path_without_git=True)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing: tool 'git' is not installed", result.stdout)
        self.assertIn("missing: GitHub repo for integration runs is not configured", result.stdout)

    def test_foundation_apply_rejects_unresolved_repo_target(self) -> None:
        result = self._run_mode(
            mode="foundation-apply",
            extra_env={
                "AWS_REGION": "ap-southeast-2",
                "TF_STATE_BUCKET": "example-bucket",
                "GITHUB_OWNER": "example-owner",
            },
            git_script=(
                "#!/usr/bin/env bash\n"
                "exit 1\n"
            ),
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Missing required integration input: GITHUB_REPO", result.stderr)

    def test_foundation_apply_reports_missing_inputs_before_oidc_probe(self) -> None:
        result = self._run_mode(
            mode="foundation-apply",
            extra_env={
                "TF_STATE_BUCKET": "example-bucket",
                "GITHUB_OWNER": "example-owner",
                "GITHUB_REPO": "example-repo",
            },
            tofu_script="#!/usr/bin/env bash\nexit 0\n",
            aws_script=(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' 'oidc probe should not run before input validation' >&2\n"
                "exit 99\n"
            ),
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Missing required integration input: AWS_REGION", result.stderr)
        self.assertNotIn("oidc probe should not run before input validation", result.stderr)

    def test_second_apply_rejects_unresolved_repo_target(self) -> None:
        result = self._run_mode(
            mode="second-apply",
            extra_env={
                "AWS_REGION": "ap-southeast-2",
                "TF_STATE_BUCKET": "example-bucket",
                "GITHUB_OWNER": "example-owner",
            },
            git_script=(
                "#!/usr/bin/env bash\n"
                "exit 1\n"
            ),
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Missing required integration input: GITHUB_REPO", result.stderr)

    def test_destroy_requires_preserved_workdir_for_original_settings(self) -> None:
        result = self._run_mode(
            mode="destroy",
            extra_env={
                "AWS_REGION": "ap-southeast-2",
                "TF_STATE_BUCKET": "example-bucket",
                "GITHUB_OWNER": "example-owner",
                "AWS_INTEGRATION_RUN_ID": "example-run-id",
            },
            git_script=(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' 'git@github.com:example-org/example-repo.git'\n"
            ),
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Destroy mode requires AWS_INTEGRATION_WORKDIR", result.stderr)

    def test_destroy_reuses_github_repo_and_oidc_settings_from_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workdir = Path(temp_dir) / "workdir"
            workdir.mkdir()
            (workdir / "integration-metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "example-run-id",
                        "github_repo": "repo-from-metadata",
                        "manage_github_oidc_provider": False,
                        "github_oidc_provider_arn": "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com",
                    }
                ),
                encoding="utf-8",
            )
            tofu_log = Path(temp_dir) / "tofu.log"
            result = self._run_mode(
                mode="destroy",
                extra_env={
                    "AWS_REGION": "ap-southeast-2",
                    "TF_STATE_BUCKET": "example-bucket",
                    "GITHUB_OWNER": "example-owner",
                    "AWS_INTEGRATION_RUN_ID": "example-run-id",
                    "AWS_INTEGRATION_WORKDIR": str(workdir),
                },
                git_script="#!/usr/bin/env bash\nexit 1\n",
                tofu_script=(
                    "#!/usr/bin/env bash\n"
                    f"printf '%s\\n' \"$*\" >> '{tofu_log}'\n"
                ),
            )

            self.assertEqual(result.returncode, 0)
            tfvars = (workdir / "integration.tfvars").read_text(encoding="utf-8")
            self.assertIn('github_repo         = "repo-from-metadata"', tfvars)
            self.assertIn("manage_github_oidc_provider = false", tfvars)
            self.assertIn(
                'github_oidc_provider_arn    = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"',
                tfvars,
            )

    def test_second_apply_reuses_existing_metadata_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workdir = Path(temp_dir) / "workdir"
            workdir.mkdir()
            (workdir / "integration-metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "example-run-id",
                        "github_repo": "repo-from-metadata",
                        "manage_github_oidc_provider": False,
                        "github_oidc_provider_arn": "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com",
                    }
                ),
                encoding="utf-8",
            )
            tofu_log = Path(temp_dir) / "tofu.log"
            result = self._run_mode(
                mode="second-apply",
                extra_env={
                    "AWS_REGION": "ap-southeast-2",
                    "TF_STATE_BUCKET": "example-bucket",
                    "GITHUB_OWNER": "example-owner",
                    "AWS_INTEGRATION_RUN_ID": "example-run-id",
                    "AWS_INTEGRATION_WORKDIR": str(workdir),
                },
                git_script="#!/usr/bin/env bash\nexit 1\n",
                tofu_script=(
                    "#!/usr/bin/env bash\n"
                    f"printf '%s\\n' \"$*\" >> '{tofu_log}'\n"
                ),
                aws_script=(
                    "#!/usr/bin/env bash\n"
                    "case \"$1 $2\" in\n"
                    "  'apprunner list-services')\n"
                    "    printf '%s\\n' 'https://example-service.awsapprunner.com'\n"
                    "    exit 0\n"
                    "    ;;\n"
                    "  *)\n"
                    "    printf '%s\\n' 'oidc probe should not run when metadata exists' >&2\n"
                    "    exit 99\n"
                    "    ;;\n"
                    "esac\n"
                ),
            )

            self.assertEqual(result.returncode, 0)
            tfvars = (workdir / "integration.tfvars").read_text(encoding="utf-8")
            self.assertIn('github_repo         = "repo-from-metadata"', tfvars)
            self.assertIn("manage_github_oidc_provider = false", tfvars)

    def test_destroy_old_metadata_falls_back_to_env_repo_and_default_oidc_management(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workdir = Path(temp_dir) / "workdir"
            workdir.mkdir()
            (workdir / "integration-metadata.json").write_text(
                json.dumps({"run_id": "legacy-run"}),
                encoding="utf-8",
            )
            result = self._run_mode(
                mode="destroy",
                extra_env={
                    "AWS_REGION": "ap-southeast-2",
                    "TF_STATE_BUCKET": "example-bucket",
                    "GITHUB_OWNER": "example-owner",
                    "GITHUB_REPO": "repo-from-env",
                    "AWS_INTEGRATION_RUN_ID": "legacy-run",
                    "AWS_INTEGRATION_WORKDIR": str(workdir),
                },
                tofu_script="#!/usr/bin/env bash\nexit 0\n",
            )

            self.assertEqual(result.returncode, 0)
            tfvars = (workdir / "integration.tfvars").read_text(encoding="utf-8")
            self.assertIn('github_repo         = "repo-from-env"', tfvars)
            self.assertIn("manage_github_oidc_provider = true", tfvars)

    def test_reused_metadata_run_id_must_match_current_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workdir = Path(temp_dir) / "workdir"
            workdir.mkdir()
            (workdir / "integration-metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "different-run-id",
                        "github_repo": "repo-from-metadata",
                        "manage_github_oidc_provider": False,
                        "github_oidc_provider_arn": "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com",
                    }
                ),
                encoding="utf-8",
            )
            result = self._run_mode(
                mode="second-apply",
                extra_env={
                    "AWS_REGION": "ap-southeast-2",
                    "TF_STATE_BUCKET": "example-bucket",
                    "GITHUB_OWNER": "example-owner",
                    "AWS_INTEGRATION_RUN_ID": "example-run-id",
                    "AWS_INTEGRATION_WORKDIR": str(workdir),
                },
                tofu_script="#!/usr/bin/env bash\nexit 0\n",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("does not match AWS_INTEGRATION_RUN_ID", result.stderr)

    def test_foundation_apply_warns_and_falls_back_when_oidc_probe_fails(self) -> None:
        result = self._run_mode(
            mode="foundation-apply",
            extra_env={
                "AWS_REGION": "ap-southeast-2",
                "TF_STATE_BUCKET": "example-bucket",
                "GITHUB_OWNER": "example-owner",
                "GITHUB_REPO": "example-repo",
                "AWS_INTEGRATION_ALLOW_OIDC_PROBE_FALLBACK": "1",
            },
            tofu_script="#!/usr/bin/env bash\nexit 0\n",
            aws_script=(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' 'simulated iam permission failure' >&2\n"
                "exit 255\n"
            ),
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("falling back to Terraform-managed GitHub OIDC provider", result.stderr)

    def test_foundation_apply_with_explicit_run_id_reuses_existing_oidc(self) -> None:
        result = self._run_mode(
            mode="foundation-apply",
            extra_env={
                "AWS_REGION": "ap-southeast-2",
                "TF_STATE_BUCKET": "example-bucket",
                "GITHUB_OWNER": "example-owner",
                "GITHUB_REPO": "example-repo",
                "AWS_INTEGRATION_RUN_ID": "explicit-run-id",
            },
            tofu_script="#!/usr/bin/env bash\nexit 0\n",
            aws_script=(
                "#!/usr/bin/env bash\n"
                "case \"$1 $2\" in\n"
                "  'iam list-open-id-connect-providers')\n"
                "    printf '%s\\n' '{\"OpenIDConnectProviderList\":[{\"Arn\":\"arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com\"}]}'\n"
                "    exit 0\n"
                "    ;;\n"
                "  'iam get-open-id-connect-provider')\n"
                "    printf '%s\\n' '{\"Url\":\"token.actions.githubusercontent.com\",\"ClientIDList\":[\"sts.amazonaws.com\"]}'\n"
                "    exit 0\n"
                "    ;;\n"
                "  *)\n"
                "    printf '%s\\n' 'unexpected aws command' >&2\n"
                "    exit 99\n"
                "    ;;\n"
                "esac\n"
            ),
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("Manage GitHub OIDC provider: false", result.stdout)

    def test_foundation_apply_aborts_on_fatal_oidc_probe_conflict(self) -> None:
        result = self._run_mode(
            mode="foundation-apply",
            extra_env={
                "AWS_REGION": "ap-southeast-2",
                "TF_STATE_BUCKET": "example-bucket",
                "GITHUB_OWNER": "example-owner",
                "GITHUB_REPO": "example-repo",
            },
            tofu_script="#!/usr/bin/env bash\nexit 0\n",
            aws_script=(
                "#!/usr/bin/env bash\n"
                "case \"$1 $2\" in\n"
                "  'iam list-open-id-connect-providers')\n"
                "    printf '%s\\n' '{\"OpenIDConnectProviderList\":[{\"Arn\":\"arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com\"}]}'\n"
                "    exit 0\n"
                "    ;;\n"
                "  'iam get-open-id-connect-provider')\n"
                "    printf '%s\\n' '{\"Url\":\"token.actions.githubusercontent.com\",\"ClientIDList\":[\"unexpected-audience\"]}'\n"
                "    exit 0\n"
                "    ;;\n"
                "esac\n"
            ),
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not include required audience", result.stderr)

    def test_preserved_metadata_reuses_original_derived_run_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workdir = Path(temp_dir) / "workdir"
            workdir.mkdir()
            (workdir / "integration-metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "example-run-id",
                        "integration_prefix": "original-prefix",
                        "service_name": "original-service",
                        "ecr_repository_name": "original-repo",
                        "image_tag": "original-image-tag",
                        "state_key": "original/state/key.tfstate",
                        "github_repo": "repo-from-metadata",
                        "manage_github_oidc_provider": False,
                        "github_oidc_provider_arn": "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com",
                    }
                ),
                encoding="utf-8",
            )
            result = self._run_mode(
                mode="second-apply",
                extra_env={
                    "AWS_REGION": "ap-southeast-2",
                    "TF_STATE_BUCKET": "example-bucket",
                    "GITHUB_OWNER": "example-owner",
                    "AWS_INTEGRATION_RUN_ID": "example-run-id",
                    "AWS_INTEGRATION_WORKDIR": str(workdir),
                },
                git_script="#!/usr/bin/env bash\nexit 1\n",
                tofu_script="#!/usr/bin/env bash\nexit 0\n",
                aws_script=(
                    "#!/usr/bin/env bash\n"
                    "case \"$1 $2\" in\n"
                    "  'apprunner list-services')\n"
                    "    printf '%s\\n' 'https://example-service.awsapprunner.com'\n"
                    "    exit 0\n"
                    "    ;;\n"
                    "  *)\n"
                    "    printf '%s\\n' 'oidc probe should not run when metadata exists' >&2\n"
                    "    exit 99\n"
                    "    ;;\n"
                    "esac\n"
                ),
            )

            self.assertEqual(result.returncode, 0)
            tfvars = (workdir / "integration.tfvars").read_text(encoding="utf-8")
            backend = (workdir / "backend.hcl").read_text(encoding="utf-8")
            self.assertIn('service_name        = "original-service"', tfvars)
            self.assertIn('apprunner_image_tag = "original-image-tag"', tfvars)
            self.assertIn('key          = "original/state/key.tfstate"', backend)

    def _run_preflight(
        self,
        extra_env: dict[str, str],
        git_script: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return self._run_mode("preflight", extra_env=extra_env, git_script=git_script)

    def _run_mode(
        self,
        mode: str,
        extra_env: dict[str, str],
        git_script: str | None = None,
        tofu_script: str | None = None,
        aws_script: str | None = None,
        path_without_git: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.pop("GITHUB_REPO", None)
        env.update(extra_env)

        with tempfile.TemporaryDirectory() as temp_dir:
            stub_dir = Path(temp_dir) / "bin"
            stub_dir.mkdir()
            if path_without_git:
                for tool_name in ("python3", "tofu", "aws", "docker", "jq"):
                    tool_path = shutil.which(tool_name, path=env["PATH"])
                    self.assertIsNotNone(tool_path)
                    tool_stub = stub_dir / tool_name
                    tool_stub.write_text(
                        "#!/usr/bin/bash\n"
                        f"exec {tool_path} \"$@\"\n",
                        encoding="utf-8",
                    )
                    tool_stub.chmod(0o755)
                env["PATH"] = str(stub_dir)

            if git_script is not None:
                git_stub = stub_dir / "git"
                git_stub.write_text(git_script, encoding="utf-8")
                git_stub.chmod(0o755)
            if tofu_script is not None:
                tofu_stub = stub_dir / "tofu"
                tofu_stub.write_text(tofu_script, encoding="utf-8")
                tofu_stub.chmod(0o755)
            if aws_script is not None:
                aws_stub = stub_dir / "aws"
                aws_stub.write_text(aws_script, encoding="utf-8")
                aws_stub.chmod(0o755)
            if any(path.exists() for path in stub_dir.iterdir()):
                env["PATH"] = f"{stub_dir}:{env['PATH']}"

            return subprocess.run(
                ["/usr/bin/bash", str(SCRIPT_PATH), mode],
                text=True,
                capture_output=True,
                check=False,
                cwd=ROOT_DIR,
                env=env,
            )


if __name__ == "__main__":
    unittest.main()
