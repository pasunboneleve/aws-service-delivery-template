import re
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT_DIR / ".github" / "workflows" / "deploy.yml"
GITHUB_SECRETS_PATH = ROOT_DIR / "infra" / "github_secrets.tf"
MAIN_TF_PATH = ROOT_DIR / "infra" / "main.tf"


class WorkflowContractsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.github_secrets_text = GITHUB_SECRETS_PATH.read_text(encoding="utf-8")
        cls.main_tf_text = MAIN_TF_PATH.read_text(encoding="utf-8")

    def test_workflow_variables_are_managed_in_terraform(self) -> None:
        variable_refs = set(re.findall(r"vars\.([A-Z0-9_]+)", self.workflow_text))
        managed_variables = set(
            re.findall(
                r'resource\s+"github_actions_variable"\s+"[^"]+"\s*\{[^}]*?variable_name\s*=\s*"([^"]+)"',
                self.github_secrets_text,
                flags=re.DOTALL,
            )
        )

        self.assertTrue(variable_refs, "expected workflow to reference GitHub Actions variables")
        self.assertSetEqual(variable_refs, managed_variables)

    def test_workflow_secrets_are_managed_in_terraform(self) -> None:
        secret_refs = set(re.findall(r"secrets\.([A-Z0-9_]+)", self.workflow_text))
        managed_secrets = set(
            re.findall(
                r'resource\s+"github_actions_secret"\s+"[^"]+"\s*\{[^}]*?secret_name\s*=\s*"([^"]+)"',
                self.github_secrets_text,
                flags=re.DOTALL,
            )
        )

        self.assertTrue(secret_refs, "expected workflow to reference GitHub Actions secrets")
        self.assertSetEqual(secret_refs, managed_secrets)

    def test_workflow_cli_usage_has_matching_iam_actions(self) -> None:
        expected_command_actions = {
            "aws apprunner list-services": "apprunner:ListServices",
            "aws apprunner update-service": "apprunner:UpdateService",
        }

        for command_snippet, action in expected_command_actions.items():
            with self.subTest(command=command_snippet, action=action):
                self.assertIn(command_snippet, self.workflow_text)
                self.assertIn(f'"{action}"', self.main_tf_text)

        self.assertIn("AccessRoleArn=${ECR_ACCESS_ROLE_ARN}", self.workflow_text)
        self.assertIn('"iam:PassRole"', self.main_tf_text)

        # The workflow does not call describe-service today; keep the role ready for
        # status checks without letting the permission drift away unnoticed.
        self.assertIn('"apprunner:DescribeService"', self.main_tf_text)

    def test_ci_does_not_create_app_runner_service_directly(self) -> None:
        forbidden_patterns = [
            "aws apprunner create-service",
            "aws apprunner start-deployment",
        ]

        for pattern in forbidden_patterns:
            with self.subTest(pattern=pattern):
                self.assertNotIn(pattern, self.workflow_text)

        self.assertIn(
            "does not exist. Run tofu apply after the bootstrap image is available.",
            self.workflow_text,
        )


if __name__ == "__main__":
    unittest.main()
