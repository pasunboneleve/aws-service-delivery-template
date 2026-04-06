import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
ECR_SCRIPT_PATH = ROOT_DIR / "scripts" / "check-ecr-image.sh"
OIDC_SCRIPT_PATH = ROOT_DIR / "scripts" / "check-github-oidc-provider.sh"
ECS_EXPRESS_SERVICE_SCRIPT_PATH = ROOT_DIR / "scripts" / "describe-ecs-express-service.sh"
ECS_SERVICE_LINKED_ROLE_SCRIPT_PATH = ROOT_DIR / "scripts" / "ensure-ecs-service-linked-role.sh"


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

    def test_missing_repository_returns_false(self) -> None:
        result = self._run_script(
            aws_script=(
                "#!/usr/bin/env bash\n"
                "echo 'An error occurred (RepositoryNotFoundException) when calling the DescribeImages operation' >&2\n"
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
                    "repository_name": "example",
                    "image_tag": "latest",
                    "aws_region": "ap-southeast-2",
                }
            )

            return subprocess.run(
                ["bash", str(ECR_SCRIPT_PATH)],
                input=payload,
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )


class GithubOidcProviderScriptContractsTest(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("jq") is None:
            self.skipTest("jq is required to exercise check-github-oidc-provider.sh")

    def test_existing_provider_returns_true_with_arn(self) -> None:
        result = self._run_script(
            aws_script=(
                "#!/usr/bin/env bash\n"
                "if [ \"$1\" = \"iam\" ] && [ \"$2\" = \"list-open-id-connect-providers\" ] && [ \"$3\" = \"--output\" ] && [ \"$4\" = \"json\" ]; then\n"
                "  cat <<'EOF'\n"
                "{\"OpenIDConnectProviderList\":[{\"Arn\":\"arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com\"}]}\n"
                "EOF\n"
                "elif [ \"$1\" = \"iam\" ] && [ \"$2\" = \"get-open-id-connect-provider\" ] && [ \"$5\" = \"--output\" ] && [ \"$6\" = \"json\" ]; then\n"
                "  cat <<'EOF'\n"
                "{\"Url\":\"token.actions.githubusercontent.com\",\"ClientIDList\":[\"sts.amazonaws.com\"]}\n"
                "EOF\n"
                "else\n"
                "  exit 2\n"
                "fi\n"
            )
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "exists": "true",
                "arn": "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com",
            },
        )

    def test_matching_provider_can_appear_later_in_the_list(self) -> None:
        result = self._run_script(
            aws_script=(
                "#!/usr/bin/env bash\n"
                "if [ \"$1\" = \"iam\" ] && [ \"$2\" = \"list-open-id-connect-providers\" ]; then\n"
                "  cat <<'EOF'\n"
                "{\"OpenIDConnectProviderList\":[{\"Arn\":\"arn:aws:iam::123456789012:oidc-provider/example.invalid\"},{\"Arn\":\"arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com\"}]}\n"
                "EOF\n"
                "elif [ \"$1\" = \"iam\" ] && [ \"$2\" = \"get-open-id-connect-provider\" ] && [ \"$4\" = \"arn:aws:iam::123456789012:oidc-provider/example.invalid\" ]; then\n"
                "  cat <<'EOF'\n"
                "{\"Url\":\"example.invalid\",\"ClientIDList\":[\"sts.amazonaws.com\"]}\n"
                "EOF\n"
                "elif [ \"$1\" = \"iam\" ] && [ \"$2\" = \"get-open-id-connect-provider\" ]; then\n"
                "  cat <<'EOF'\n"
                "{\"Url\":\"token.actions.githubusercontent.com\",\"ClientIDList\":[\"sts.amazonaws.com\"]}\n"
                "EOF\n"
                "else\n"
                "  exit 2\n"
                "fi\n"
            )
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "exists": "true",
                "arn": "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com",
            },
        )

    def test_unreadable_provider_is_skipped_if_later_match_exists(self) -> None:
        result = self._run_script(
            aws_script=(
                "#!/usr/bin/env bash\n"
                "if [ \"$1\" = \"iam\" ] && [ \"$2\" = \"list-open-id-connect-providers\" ]; then\n"
                "  cat <<'EOF'\n"
                "{\"OpenIDConnectProviderList\":[{\"Arn\":\"arn:aws:iam::123456789012:oidc-provider/inaccessible.example\"},{\"Arn\":\"arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com\"}]}\n"
                "EOF\n"
                "elif [ \"$1\" = \"iam\" ] && [ \"$2\" = \"get-open-id-connect-provider\" ] && [ \"$4\" = \"arn:aws:iam::123456789012:oidc-provider/inaccessible.example\" ]; then\n"
                "  echo 'AccessDenied for inaccessible.example' >&2\n"
                "  exit 255\n"
                "elif [ \"$1\" = \"iam\" ] && [ \"$2\" = \"get-open-id-connect-provider\" ]; then\n"
                "  cat <<'EOF'\n"
                "{\"Url\":\"token.actions.githubusercontent.com\",\"ClientIDList\":[\"sts.amazonaws.com\"]}\n"
                "EOF\n"
                "else\n"
                "  exit 2\n"
                "fi\n"
            )
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "exists": "true",
                "arn": "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com",
            },
        )
        self.assertIn("OIDC provider check warning", result.stderr)

    def test_unreadable_target_provider_exits_non_zero(self) -> None:
        result = self._run_script(
            aws_script=(
                "#!/usr/bin/env bash\n"
                "if [ \"$1\" = \"iam\" ] && [ \"$2\" = \"list-open-id-connect-providers\" ]; then\n"
                "  cat <<'EOF'\n"
                "{\"OpenIDConnectProviderList\":[{\"Arn\":\"arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com\"}]}\n"
                "EOF\n"
                "elif [ \"$1\" = \"iam\" ] && [ \"$2\" = \"get-open-id-connect-provider\" ]; then\n"
                "  echo 'AccessDenied for token.actions.githubusercontent.com' >&2\n"
                "  exit 255\n"
                "else\n"
                "  exit 2\n"
                "fi\n"
            )
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unable to read the existing target provider", result.stderr)

    def test_provider_missing_url_is_skipped_if_later_match_exists(self) -> None:
        result = self._run_script(
            aws_script=(
                "#!/usr/bin/env bash\n"
                "if [ \"$1\" = \"iam\" ] && [ \"$2\" = \"list-open-id-connect-providers\" ]; then\n"
                "  cat <<'EOF'\n"
                "{\"OpenIDConnectProviderList\":[{\"Arn\":\"arn:aws:iam::123456789012:oidc-provider/malformed.example\"},{\"Arn\":\"arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com\"}]}\n"
                "EOF\n"
                "elif [ \"$1\" = \"iam\" ] && [ \"$2\" = \"get-open-id-connect-provider\" ] && [ \"$4\" = \"arn:aws:iam::123456789012:oidc-provider/malformed.example\" ]; then\n"
                "  cat <<'EOF'\n"
                "{}\n"
                "EOF\n"
                "elif [ \"$1\" = \"iam\" ] && [ \"$2\" = \"get-open-id-connect-provider\" ]; then\n"
                "  cat <<'EOF'\n"
                "{\"Url\":\"token.actions.githubusercontent.com\",\"ClientIDList\":[\"sts.amazonaws.com\"]}\n"
                "EOF\n"
                "else\n"
                "  exit 2\n"
                "fi\n"
            )
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "exists": "true",
                "arn": "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com",
            },
        )
        self.assertIn("provider response did not contain Url", result.stderr)

    def test_missing_provider_returns_false(self) -> None:
        result = self._run_script(
            aws_script=(
                "#!/usr/bin/env bash\n"
                "if [ \"$1\" = \"iam\" ] && [ \"$2\" = \"list-open-id-connect-providers\" ]; then\n"
                "  cat <<'EOF'\n"
                "{\"OpenIDConnectProviderList\":[{\"Arn\":\"arn:aws:iam::123456789012:oidc-provider/example.invalid\"}]}\n"
                "EOF\n"
                "elif [ \"$1\" = \"iam\" ] && [ \"$2\" = \"get-open-id-connect-provider\" ]; then\n"
                "  cat <<'EOF'\n"
                "{\"Url\":\"example.invalid\",\"ClientIDList\":[\"sts.amazonaws.com\"]}\n"
                "EOF\n"
                "else\n"
                "  exit 2\n"
                "fi\n"
            )
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {"exists": "false", "arn": ""})

    def test_matching_url_with_wrong_audience_exits_non_zero(self) -> None:
        result = self._run_script(
            aws_script=(
                "#!/usr/bin/env bash\n"
                "if [ \"$1\" = \"iam\" ] && [ \"$2\" = \"list-open-id-connect-providers\" ]; then\n"
                "  cat <<'EOF'\n"
                "{\"OpenIDConnectProviderList\":[{\"Arn\":\"arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com\"}]}\n"
                "EOF\n"
                "elif [ \"$1\" = \"iam\" ] && [ \"$2\" = \"get-open-id-connect-provider\" ]; then\n"
                "  cat <<'EOF'\n"
                "{\"Url\":\"token.actions.githubusercontent.com\",\"ClientIDList\":[\"not-sts.amazonaws.com\"]}\n"
                "EOF\n"
                "else\n"
                "  exit 2\n"
                "fi\n"
            )
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not include required audience", result.stderr)

    def test_empty_provider_list_returns_false(self) -> None:
        result = self._run_script(
            aws_script=(
                "#!/usr/bin/env bash\n"
                "if [ \"$1\" = \"iam\" ] && [ \"$2\" = \"list-open-id-connect-providers\" ]; then\n"
                "  cat <<'EOF'\n"
                "{\"OpenIDConnectProviderList\":[]}\n"
                "EOF\n"
                "else\n"
                "  exit 2\n"
                "fi\n"
            )
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {"exists": "false", "arn": ""})

    def test_credential_or_permission_failure_exits_non_zero(self) -> None:
        result = self._run_script(
            aws_script=(
                "#!/usr/bin/env bash\n"
                "echo 'An error occurred (AccessDenied) when calling the ListOpenIDConnectProviders operation' >&2\n"
                "exit 255\n"
            )
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OIDC provider check failed:", result.stderr)

    def test_unexpected_list_failure_exits_non_zero(self) -> None:
        result = self._run_script(
            aws_script=(
                "#!/usr/bin/env bash\n"
                "echo 'panic: malformed response' >&2\n"
                "exit 255\n"
            )
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OIDC provider check failed:", result.stderr)

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
                    "url": "https://token.actions.githubusercontent.com",
                    "audience": "sts.amazonaws.com",
                }
            )

            return subprocess.run(
                ["bash", str(OIDC_SCRIPT_PATH)],
                input=payload,
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )


class EcsExpressServiceScriptContractsTest(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("jq") is None:
            self.skipTest("jq is required to exercise describe-ecs-express-service.sh")

    def test_existing_service_returns_public_endpoint(self) -> None:
        result = self._run_script(
            aws_script=(
                "#!/usr/bin/env bash\n"
                "cat <<'EOF'\n"
                '{"service":{"activeConfigurations":[{"ingressPaths":[{"accessType":"PUBLIC","endpoint":"https://example.express.aws"},{"accessType":"PRIVATE","endpoint":"https://internal.example"}]}]}}\n'
                "EOF\n"
            )
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {"endpoint": "https://example.express.aws"})

    def test_missing_service_returns_empty_endpoint(self) -> None:
        result = self._run_script(
            aws_script=(
                "#!/usr/bin/env bash\n"
                "echo 'An error occurred (ResourceNotFoundException) when calling the DescribeExpressGatewayService operation' >&2\n"
                "exit 255\n"
            )
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {"endpoint": ""})

    def test_auth_or_api_failure_exits_non_zero(self) -> None:
        result = self._run_script(
            aws_script=(
                "#!/usr/bin/env bash\n"
                "echo 'An error occurred (AccessDeniedException) when calling the DescribeExpressGatewayService operation' >&2\n"
                "exit 255\n"
            )
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("AccessDeniedException", result.stderr)

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
                    "service_arn": "arn:aws:ecs:ap-southeast-2:123456789012:express-gateway-service/example",
                    "aws_region": "ap-southeast-2",
                }
            )

            return subprocess.run(
                ["bash", str(ECS_EXPRESS_SERVICE_SCRIPT_PATH)],
                input=payload,
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )


class EcsServiceLinkedRoleScriptContractsTest(unittest.TestCase):
    def setUp(self) -> None:
        pass

    def test_existing_role_exits_zero(self) -> None:
        result = self._run_script(
            aws_script=(
                "#!/usr/bin/env bash\n"
                "cat <<'EOF'\n"
                '{"Role":{"Arn":"arn:aws:iam::123456789012:role/aws-service-role/ecs.amazonaws.com/AWSServiceRoleForECS"}}\n'
                "EOF\n"
            )
        )

        self.assertEqual(result.returncode, 0)

    def test_missing_role_is_created(self) -> None:
        result = self._run_script(
            aws_script=(
                "#!/usr/bin/env bash\n"
                "if [ \"$1\" = \"iam\" ] && [ \"$2\" = \"get-role\" ]; then\n"
                "  echo 'An error occurred (NoSuchEntity) when calling the GetRole operation' >&2\n"
                "  exit 255\n"
                "fi\n"
                "if [ \"$1\" = \"iam\" ] && [ \"$2\" = \"create-service-linked-role\" ]; then\n"
                "  exit 0\n"
                "fi\n"
                "exit 2\n"
            )
        )

        self.assertEqual(result.returncode, 0)

    def test_already_exists_race_exits_zero(self) -> None:
        result = self._run_script(
            aws_script=(
                "#!/usr/bin/env bash\n"
                "if [ \"$1\" = \"iam\" ] && [ \"$2\" = \"get-role\" ]; then\n"
                "  echo 'An error occurred (NoSuchEntity) when calling the GetRole operation' >&2\n"
                "  exit 255\n"
                "fi\n"
                "if [ \"$1\" = \"iam\" ] && [ \"$2\" = \"create-service-linked-role\" ]; then\n"
                "  echo 'An error occurred (InvalidInput) when calling the CreateServiceLinkedRole operation: Service role name AWSServiceRoleForECS has been taken in this account, please try a different suffix.' >&2\n"
                "  exit 255\n"
                "fi\n"
                "exit 2\n"
            )
        )

        self.assertEqual(result.returncode, 0)

    def test_auth_or_api_failure_exits_non_zero(self) -> None:
        result = self._run_script(
            aws_script=(
                "#!/usr/bin/env bash\n"
                "echo 'An error occurred (AccessDenied) when calling the GetRole operation' >&2\n"
                "exit 255\n"
            )
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ECS service-linked role ensure failed during get-role:", result.stderr)

    def _run_script(self, aws_script: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            stub_dir = Path(temp_dir) / "bin"
            stub_dir.mkdir()

            aws_stub = stub_dir / "aws"
            aws_stub.write_text(aws_script, encoding="utf-8")
            aws_stub.chmod(0o755)

            env = os.environ.copy()
            env["PATH"] = f"{stub_dir}:{env['PATH']}"

            return subprocess.run(
                ["bash", str(ECS_SERVICE_LINKED_ROLE_SCRIPT_PATH)],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )


if __name__ == "__main__":
    unittest.main()
