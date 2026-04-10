import os
import json
import socket
import shutil
import subprocess
import tempfile
import time
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

    def test_preflight_allows_env_only_github_auth_without_prod_tfvars(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = self._run_preflight(
                extra_env={
                    "AWS_REGION": "ap-southeast-2",
                    "TF_STATE_BUCKET": "example-bucket",
                    "GITHUB_OWNER": "example-owner",
                    "GITHUB_REPO": "example-repo",
                    "GITHUB_TOKEN": "ghs_env_token",
                    "AWS_PROFILE": "example-profile",
                    "AWS_INTEGRATION_PROD_TFVARS_PATH": str(Path(temp_dir) / "missing-prod.tfvars"),
                },
            )

            self.assertEqual(result.returncode, 0)
            self.assertIn("ready: GitHub provider auth is configured via GITHUB_TOKEN", result.stdout)
            self.assertIn("note:", result.stdout)
            self.assertNotIn("missing: ", result.stdout)

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

    def test_foundation_apply_passes_github_token_from_environment_to_tofu(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tofu_log = Path(temp_dir) / "tofu.log"
            result = self._run_mode(
                mode="foundation-apply",
                extra_env={
                    "AWS_REGION": "ap-southeast-2",
                    "TF_STATE_BUCKET": "example-bucket",
                    "GITHUB_OWNER": "example-owner",
                    "GITHUB_REPO": "example-repo",
                    "GITHUB_TOKEN": "ghs_env_token",
                    "AWS_INTEGRATION_ALLOW_OIDC_PROBE_FALLBACK": "1",
                },
                tofu_script=(
                    "#!/usr/bin/env bash\n"
                    f"printf '%s\\n' \"$TF_VAR_github_token|$*\" >> '{tofu_log}'\n"
                    "exit 0\n"
                ),
                aws_script=(
                    "#!/usr/bin/env bash\n"
                    "printf '%s\\n' 'simulated iam permission failure' >&2\n"
                    "exit 255\n"
                ),
            )

            self.assertEqual(result.returncode, 0)
            tofu_lines = tofu_log.read_text(encoding="utf-8")
            self.assertIn("ghs_env_token|init -reconfigure", tofu_lines)
            self.assertIn("ghs_env_token|apply -auto-approve", tofu_lines)

    def test_foundation_apply_passes_github_token_from_prod_tfvars_to_tofu(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tofu_log = Path(temp_dir) / "tofu.log"
            prod_tfvars_path = Path(temp_dir) / "prod.tfvars"
            prod_tfvars_path.write_text('github_token = "ghs_from_tfvars"\n', encoding="utf-8")
            result = self._run_mode(
                mode="foundation-apply",
                extra_env={
                    "AWS_REGION": "ap-southeast-2",
                    "TF_STATE_BUCKET": "example-bucket",
                    "GITHUB_OWNER": "example-owner",
                    "GITHUB_REPO": "example-repo",
                    "AWS_INTEGRATION_ALLOW_OIDC_PROBE_FALLBACK": "1",
                    "AWS_INTEGRATION_PROD_TFVARS_PATH": str(prod_tfvars_path),
                },
                tofu_script=(
                    "#!/usr/bin/env bash\n"
                    f"printf '%s\\n' \"$TF_VAR_github_token|$*\" >> '{tofu_log}'\n"
                    "exit 0\n"
                ),
                aws_script=(
                    "#!/usr/bin/env bash\n"
                    "printf '%s\\n' 'simulated iam permission failure' >&2\n"
                    "exit 255\n"
                ),
            )

            self.assertEqual(result.returncode, 0)
            tofu_lines = tofu_log.read_text(encoding="utf-8")
            self.assertIn("ghs_from_tfvars|init -reconfigure", tofu_lines)
            self.assertIn("ghs_from_tfvars|apply -auto-approve", tofu_lines)

    def test_foundation_apply_passes_github_token_from_heredoc_prod_tfvars_to_tofu(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tofu_log = Path(temp_dir) / "tofu.log"
            prod_tfvars_path = Path(temp_dir) / "prod.tfvars"
            prod_tfvars_path.write_text(
                "github_token = <<EOF\r\n"
                "ghs_from_heredoc\r\n"
                "EOF\r\n"
                "// trailing comment style should not matter elsewhere\r\n",
                encoding="utf-8",
            )
            result = self._run_mode(
                mode="foundation-apply",
                extra_env={
                    "AWS_REGION": "ap-southeast-2",
                    "TF_STATE_BUCKET": "example-bucket",
                    "GITHUB_OWNER": "example-owner",
                    "GITHUB_REPO": "example-repo",
                    "AWS_INTEGRATION_ALLOW_OIDC_PROBE_FALLBACK": "1",
                    "AWS_INTEGRATION_PROD_TFVARS_PATH": str(prod_tfvars_path),
                },
                tofu_script=(
                    "#!/usr/bin/env bash\n"
                    f"printf '%s\\n' \"$TF_VAR_github_token|$*\" >> '{tofu_log}'\n"
                    "exit 0\n"
                ),
                aws_script=(
                    "#!/usr/bin/env bash\n"
                    "printf '%s\\n' 'simulated iam permission failure' >&2\n"
                    "exit 255\n"
                ),
            )

            self.assertEqual(result.returncode, 0)
            tofu_lines = tofu_log.read_text(encoding="utf-8")
            self.assertIn("ghs_from_heredoc|init -reconfigure", tofu_lines)
            self.assertIn("ghs_from_heredoc|apply -auto-approve", tofu_lines)

    def test_preflight_accepts_double_quoted_prod_tfvars_with_slash_comments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            prod_tfvars_path = Path(temp_dir) / "prod.tfvars"
            prod_tfvars_path.write_text(
                "// integration auth token\r\n"
                'github_token = "ghs_from_comment_style" // trailing comment\r\n',
                encoding="utf-8",
            )
            result = self._run_preflight(
                extra_env={
                    "AWS_REGION": "ap-southeast-2",
                    "TF_STATE_BUCKET": "example-bucket",
                    "GITHUB_OWNER": "example-owner",
                    "GITHUB_REPO": "example-repo",
                    "AWS_PROFILE": "example-profile",
                    "AWS_INTEGRATION_PROD_TFVARS_PATH": str(prod_tfvars_path),
                },
            )

            self.assertEqual(result.returncode, 0)
            self.assertIn("ready: GitHub provider auth is configured via", result.stdout)

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
                        "create_github_oidc_provider": False,
                        "service_arn": "arn:aws:ecs:ap-southeast-2:123456789012:express-gateway-service/example-service",
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
            self.assertIn("create_github_oidc_provider = false", tfvars)

    def test_second_apply_reuses_existing_metadata_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workdir = Path(temp_dir) / "workdir"
            workdir.mkdir()
            (workdir / "integration-metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "example-run-id",
                        "github_repo": "repo-from-metadata",
                        "create_github_oidc_provider": False,
                        "service_arn": "arn:aws:ecs:ap-southeast-2:123456789012:express-gateway-service/example-service",
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
                    "  'ecs describe-express-gateway-service')\n"
                    "    printf '%s\\n' 'https://example-service.express.aws'\n"
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
            self.assertIn("create_github_oidc_provider = false", tfvars)
            self.assertIn("ecr_force_delete    = true", tfvars)

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
            self.assertIn("create_github_oidc_provider = true", tfvars)

    def test_reused_metadata_run_id_must_match_current_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workdir = Path(temp_dir) / "workdir"
            workdir.mkdir()
            (workdir / "integration-metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "different-run-id",
                        "github_repo": "repo-from-metadata",
                        "create_github_oidc_provider": False,
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
        self.assertIn("Create GitHub OIDC provider: false", result.stdout)

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
                        "create_github_oidc_provider": False,
                        "service_arn": "arn:aws:ecs:ap-southeast-2:123456789012:express-gateway-service/original-service",
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
                    "  'ecs describe-express-gateway-service')\n"
                    "    printf '%s\\n' 'https://example-service.express.aws'\n"
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
            self.assertIn('ecs_express_image_tag = "original-image-tag"', tfvars)
            self.assertIn("ecr_force_delete    = true", tfvars)
            self.assertIn('key          = "original/state/key.tfstate"', backend)

    def test_verify_reinitializes_isolated_backend_before_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workdir = Path(temp_dir) / "workdir"
            workdir.mkdir()
            (workdir / "integration-metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "verify-run",
                        "github_repo": "repo-from-metadata",
                        "service_arn": "arn:aws:ecs:ap-southeast-2:123456789012:express-gateway-service/verify-run",
                    }
                ),
                encoding="utf-8",
            )
            tofu_log = Path(temp_dir) / "tofu.log"
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            fixture_url = f"http://127.0.0.1:{port}"
            server = subprocess.Popen(
                [
                    "python3",
                    str(ROOT_DIR / "integration-fixture" / "server.py"),
                ],
                env={**os.environ, "PORT": str(port)},
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                for _ in range(20):
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                            break
                    except OSError:
                        time.sleep(0.05)
                        continue
                else:
                    self.fail(f"fixture server did not start listening on 127.0.0.1:{port}")
                result = self._run_mode(
                    mode="verify",
                    extra_env={
                        "AWS_REGION": "ap-southeast-2",
                        "TF_STATE_BUCKET": "example-bucket",
                        "GITHUB_OWNER": "example-owner",
                        "AWS_INTEGRATION_RUN_ID": "verify-run",
                        "AWS_INTEGRATION_WORKDIR": str(workdir),
                    },
                    tofu_script=(
                        "#!/usr/bin/env bash\n"
                        f"printf '%s\\n' \"$*\" >> '{tofu_log}'\n"
                        "if [ \"$1\" = 'output' ]; then\n"
                        f"  printf '%s\\n' '{fixture_url}'\n"
                        "fi\n"
                    ),
                )
            finally:
                server.terminate()
                server.wait(timeout=5)

            self.assertEqual(result.returncode, 0)
            tofu_lines = tofu_log.read_text(encoding="utf-8")
            self.assertIn("init -reconfigure", tofu_lines)
            self.assertIn("output -raw service_url", tofu_lines)

    def test_verify_falls_back_to_aws_when_tofu_init_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workdir = Path(temp_dir) / "workdir"
            workdir.mkdir()
            (workdir / "integration-metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": "verify-run",
                        "github_repo": "repo-from-metadata",
                        "service_arn": "arn:aws:ecs:ap-southeast-2:123456789012:express-gateway-service/verify-run",
                    }
                ),
                encoding="utf-8",
            )
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            fixture_url = f"http://127.0.0.1:{port}"
            server = subprocess.Popen(
                [
                    "python3",
                    str(ROOT_DIR / "integration-fixture" / "server.py"),
                ],
                env={**os.environ, "PORT": str(port)},
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                for _ in range(20):
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                            break
                    except OSError:
                        time.sleep(0.05)
                        continue
                else:
                    self.fail(f"fixture server did not start listening on 127.0.0.1:{port}")
                result = self._run_mode(
                    mode="verify",
                    extra_env={
                        "AWS_REGION": "ap-southeast-2",
                        "TF_STATE_BUCKET": "example-bucket",
                        "GITHUB_OWNER": "example-owner",
                        "AWS_INTEGRATION_RUN_ID": "verify-run",
                        "AWS_INTEGRATION_WORKDIR": str(workdir),
                    },
                    tofu_script=(
                        "#!/usr/bin/env bash\n"
                        "if [ \"$1\" = 'init' ]; then\n"
                        "  printf '%s\\n' 'simulated init failure' >&2\n"
                        "  exit 1\n"
                        "fi\n"
                        "if [ \"$1\" = 'output' ]; then\n"
                        "  printf '%s\\n' 'tofu output should not run after init failure' >&2\n"
                        "  exit 99\n"
                        "fi\n"
                        "exit 1\n"
                    ),
                    aws_script=(
                        "#!/usr/bin/env bash\n"
                        "case \"$1 $2\" in\n"
                        "  'ecs describe-express-gateway-service')\n"
                        f"    printf '%s\\n' '{fixture_url}'\n"
                        "    exit 0\n"
                        "    ;;\n"
                        "  *)\n"
                        "    printf '%s\\n' 'unexpected aws command' >&2\n"
                        "    exit 99\n"
                        "    ;;\n"
                        "esac\n"
                    ),
                )
            finally:
                server.terminate()
                server.wait(timeout=5)

            self.assertEqual(result.returncode, 0)
            self.assertIn("Fixture verification passed", result.stderr)
            tofu_stderr_log = (workdir / "tofu-service-url.stderr.log").read_text(encoding="utf-8")
            self.assertIn("Command failed: tofu init -reconfigure", tofu_stderr_log)
            self.assertNotIn("tofu output should not run after init failure", tofu_stderr_log)

    def test_failed_destroy_records_cleanup_failure_in_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workdir = Path(temp_dir) / "workdir"
            workdir.mkdir()
            (workdir / "integration-metadata.json").write_text(
                json.dumps({"run_id": "destroy-run", "github_repo": "repo-from-metadata"}),
                encoding="utf-8",
            )
            result = self._run_mode(
                mode="destroy",
                extra_env={
                    "AWS_REGION": "ap-southeast-2",
                    "TF_STATE_BUCKET": "example-bucket",
                    "GITHUB_OWNER": "example-owner",
                    "AWS_INTEGRATION_RUN_ID": "destroy-run",
                    "AWS_INTEGRATION_WORKDIR": str(workdir),
                    "AWS_INTEGRATION_SIMULATE_FAILURE_AT": "destroy",
                },
            )

            self.assertNotEqual(result.returncode, 0)
            summary = json.loads((workdir / "cleanup-status.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["cleanup_status"], "failed")
            self.assertEqual(summary["primary_step"], "destroy")

    def test_destroy_emits_green_label_on_success_when_color_forced(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workdir = Path(temp_dir) / "workdir"
            workdir.mkdir()
            (workdir / "integration-metadata.json").write_text(
                json.dumps({"run_id": "destroy-run", "github_repo": "repo-from-metadata"}),
                encoding="utf-8",
            )
            result = self._run_mode(
                mode="destroy",
                extra_env={
                    "AWS_REGION": "ap-southeast-2",
                    "TF_STATE_BUCKET": "example-bucket",
                    "GITHUB_OWNER": "example-owner",
                    "AWS_INTEGRATION_RUN_ID": "destroy-run",
                    "AWS_INTEGRATION_WORKDIR": str(workdir),
                    "AWS_INTEGRATION_FORCE_COLOR": "1",
                },
                tofu_script="#!/usr/bin/env bash\nexit 0\n",
            )

            self.assertEqual(result.returncode, 0)
            self.assertIn("\x1b[32m[destroy]\x1b[0m Destroy succeeded.", result.stderr)

    def test_destroy_emits_red_label_on_failure_when_color_forced(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workdir = Path(temp_dir) / "workdir"
            workdir.mkdir()
            (workdir / "integration-metadata.json").write_text(
                json.dumps({"run_id": "destroy-run", "github_repo": "repo-from-metadata"}),
                encoding="utf-8",
            )
            result = self._run_mode(
                mode="destroy",
                extra_env={
                    "AWS_REGION": "ap-southeast-2",
                    "TF_STATE_BUCKET": "example-bucket",
                    "GITHUB_OWNER": "example-owner",
                    "AWS_INTEGRATION_RUN_ID": "destroy-run",
                    "AWS_INTEGRATION_WORKDIR": str(workdir),
                    "AWS_INTEGRATION_FORCE_COLOR": "1",
                },
                tofu_script=(
                    "#!/usr/bin/env bash\n"
                    "if [ \"$1\" = 'destroy' ]; then\n"
                    "  exit 1\n"
                    "fi\n"
                    "exit 0\n"
                ),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("\x1b[31m[destroy]\x1b[0m Integration runner failed with exit code 1.", result.stderr)

    def test_destroy_streams_live_tofu_output_to_stderr_and_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workdir = Path(temp_dir) / "workdir"
            workdir.mkdir()
            (workdir / "integration-metadata.json").write_text(
                json.dumps({"run_id": "destroy-run", "github_repo": "repo-from-metadata"}),
                encoding="utf-8",
            )
            result = self._run_mode(
                mode="destroy",
                extra_env={
                    "AWS_REGION": "ap-southeast-2",
                    "TF_STATE_BUCKET": "example-bucket",
                    "GITHUB_OWNER": "example-owner",
                    "AWS_INTEGRATION_RUN_ID": "destroy-run",
                    "AWS_INTEGRATION_WORKDIR": str(workdir),
                },
                tofu_script=(
                    "#!/usr/bin/env bash\n"
                    "if [ \"$1\" = 'destroy' ]; then\n"
                    "  printf '%s\\n' 'aws_cloudformation_stack.ecs_express_service[0]: Still destroying... [10s elapsed]'\n"
                    "  exit 0\n"
                    "fi\n"
                    "exit 0\n"
                ),
            )

            self.assertEqual(result.returncode, 0)
            self.assertIn("Still destroying... [10s elapsed]", result.stderr)
            destroy_log = (workdir / "destroy.log").read_text(encoding="utf-8")
            self.assertIn("Still destroying... [10s elapsed]", destroy_log)

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
        for name in (
            "AWS_REGION",
            "TF_STATE_BUCKET",
            "GITHUB_OWNER",
            "GITHUB_REPO",
            "GITHUB_TOKEN",
            "TF_VAR_github_token",
            "AWS_PROFILE",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "AWS_INTEGRATION_RUN_ID",
            "AWS_INTEGRATION_WORKDIR",
            "AWS_INTEGRATION_KEEP_WORKDIR",
            "AWS_INTEGRATION_AUTO_APPROVE",
            "AWS_INTEGRATION_AWS_ACCOUNT_ID",
            "AWS_INTEGRATION_CLEANUP_TIMEOUT_SECONDS",
            "AWS_INTEGRATION_SIMULATE_FAILURE_AT",
            "AWS_INTEGRATION_VERIFY_PATH",
            "AWS_INTEGRATION_ALLOW_OIDC_PROBE_FALLBACK",
            "AWS_INTEGRATION_PROD_TFVARS_PATH",
            "AWS_INTEGRATION_FORCE_COLOR",
        ):
            env.pop(name, None)
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
