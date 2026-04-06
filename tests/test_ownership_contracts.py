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

    def test_terraform_owns_ecs_express_service(self) -> None:
        self.assertIn('resource "aws_cloudformation_stack" "ecs_express_service"', self.main_tf_text)
        self.assertIn("AWS::ECS::ExpressGatewayService", self.main_tf_text)
        self.assertIn('ignore_changes = [parameters["ImageUri"]]', self.main_tf_text)

    def test_github_actions_updates_ecs_express_service_directly(self) -> None:
        self.assertIn("aws ecs update-express-gateway-service", self.workflow_text)
        self.assertIn("AWS_ECS_EXPRESS_SERVICE_ARN", self.workflow_text)

    def test_workflow_fails_when_runtime_service_is_missing(self) -> None:
        self.assertIn('if [ "${STATUS}" = "None" ] || [ -z "${STATUS}" ]; then', self.workflow_text)
        self.assertIn(
            "ECS Express service ${AWS_ECS_EXPRESS_SERVICE_ARN} does not exist. Run tofu apply after the bootstrap image is available.",
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
