import re
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
MAIN_TF_PATH = ROOT_DIR / "infra" / "main.tf"
WORKFLOW_PATH = ROOT_DIR / ".github" / "workflows" / "deploy.yml"
README_PATH = ROOT_DIR / "README.md"
DEPLOYMENT_DOC_PATH = ROOT_DIR / "infra" / "DEPLOYMENT.md"


class OwnershipContractsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.main_tf_text = MAIN_TF_PATH.read_text(encoding="utf-8")
        cls.workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.readme_text = README_PATH.read_text(encoding="utf-8")
        cls.deployment_doc_text = DEPLOYMENT_DOC_PATH.read_text(encoding="utf-8")

    def test_terraform_owns_app_runner_service(self) -> None:
        self.assertIn('resource "aws_apprunner_service" "service"', self.main_tf_text)

    def test_app_runner_image_drift_is_ignored_after_bootstrap(self) -> None:
        self.assertIn(
            "ignore_changes = [source_configuration[0].image_repository[0].image_identifier]",
            self.main_tf_text,
        )

    def test_workflow_fails_when_runtime_service_is_missing(self) -> None:
        self.assertIn('if [ "${SERVICE_ARN}" = "None" ]; then', self.workflow_text)
        self.assertIn(
            "App Runner service ${AWS_APP_RUNNER_SERVICE_NAME} does not exist. Run tofu apply after the bootstrap image is available.",
            self.workflow_text,
        )

    def test_docs_describe_same_two_phase_bootstrap_flow(self) -> None:
        for name, text in {
            "README": self.readme_text,
            "DEPLOYMENT": self.deployment_doc_text,
        }.items():
            with self.subTest(document=name):
                self.assertIn("tofu apply", text)
                self.assertRegex(text, r"(?s)(push.*bootstrap.*image|push.*populates `latest`|Push.*bootstrap.*image)")
                self.assertRegex(text, r"(?s)Run `tofu apply` again|rerun:\s*```bash\s*tofu apply")


if __name__ == "__main__":
    unittest.main()
