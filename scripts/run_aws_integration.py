#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, parse, request


EXIT_CLEANUP_SKIPPED = 98
EXIT_OIDC_PROBE_OPTIONAL_FALLBACK = 3
VALID_MODES = {
    "plan",
    "preflight",
    "run",
    "foundation-apply",
    "bootstrap-publish",
    "second-apply",
    "verify",
    "destroy",
}

COLOR_RESET = "\033[0m"
COLOR_RED = "\033[31m"
COLOR_GREEN = "\033[32m"
COLOR_YELLOW = "\033[33m"


class RunnerError(Exception):
    def __init__(self, message: str, exit_code: int = 1):
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


class CleanupSkipped(RunnerError):
    def __init__(self, message: str):
        super().__init__(message, EXIT_CLEANUP_SKIPPED)


@dataclass
class ProbeResult:
    status: str
    arn: str | None = None
    message: str | None = None


class AwsIntegrationRunner:
    def __init__(self, argv: list[str]) -> None:
        self.root_dir = Path(__file__).resolve().parents[1]
        self.infra_dir = self.root_dir / "infra"
        self.repo_name = self.root_dir.name
        self.mode = argv[1] if len(argv) > 1 else "plan"
        self.run_id_explicit = bool(os.environ.get("AWS_INTEGRATION_RUN_ID"))
        run_id_raw = os.environ.get("AWS_INTEGRATION_RUN_ID") or self._default_run_id()
        self.run_id = self._slugify(run_id_raw)
        self.workdir = Path(os.environ["AWS_INTEGRATION_WORKDIR"]) if os.environ.get("AWS_INTEGRATION_WORKDIR") else None
        self.keep_workdir = os.environ.get("AWS_INTEGRATION_KEEP_WORKDIR", "0")
        self.cleanup_timeout_seconds = int(os.environ.get("AWS_INTEGRATION_CLEANUP_TIMEOUT_SECONDS", "900"))
        self.simulated_failure_steps = {
            step.strip() for step in os.environ.get("AWS_INTEGRATION_SIMULATE_FAILURE_AT", "").split(",") if step.strip()
        }
        self.fixture_dir = self.root_dir / "integration-fixture"

        self.workdir_created = False
        self.integration_prefix = ""
        self.service_name = ""
        self.ecr_repository_name = ""
        self.image_tag = ""
        self.state_key = ""
        self.tfvars_path: Path | None = None
        self.backend_config_path: Path | None = None
        self.metadata_path: Path | None = None
        self.verify_response_path: Path | None = None
        self.remote_image_uri = ""
        self.service_arn_value = ""

        self.github_repo_value = ""
        self.github_token_value = ""
        self.manage_github_oidc_provider = True
        self.github_oidc_provider_arn_value = ""

        self.current_step = "startup"
        self.primary_failure_step = ""
        self.original_exit_code = 0
        self.cleanup_required = False
        self.cleanup_attempted = False
        self.cleanup_exit_code = 0
        self.tofu_destroy_log_path: Path | None = None
        self.cleanup_status_path: Path | None = None
        self.enable_color = os.environ.get("AWS_INTEGRATION_FORCE_COLOR") == "1" or sys.stderr.isatty()

    def prod_tfvars_path(self) -> Path:
        override = os.environ.get("AWS_INTEGRATION_PROD_TFVARS_PATH")
        if override:
            return Path(override)
        return self.infra_dir / "prod.tfvars"

    def _default_run_id(self) -> str:
        import datetime

        return datetime.datetime.now().strftime("%Y%m%d%H%M%S") + f"-{os.getpid()}"

    def _slugify(self, value: str) -> str:
        lowered = value.lower()
        cleaned = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
        return cleaned or "integration-run"

    def _trim_name(self, value: str) -> str:
        if len(value) <= 40:
            return value
        import hashlib

        h = hashlib.sha256(value.encode()).hexdigest()[:8]
        # Keep 15 from start, 14 from end, and 8 for hash plus 2 hyphens = 39 or 40
        return f"{value[:15]}-{h}-{value[-14:]}"[:40]

    def usage(self) -> str:
        return """Usage:
  ./scripts/run-aws-integration.sh [plan|preflight|run|foundation-apply|bootstrap-publish|second-apply|verify|destroy]

This is the Phase 2 AWS integration runner skeleton.
Current behavior:
  - creates an isolated temp workdir
  - optionally runs a local readiness preflight with no AWS calls
  - derives unique naming and state paths for an integration run
  - materializes isolated backend, tfvars, and metadata files
  - prints the intended command sequence and current TODO boundaries
  - optionally runs the end-to-end integration sequence with failure cleanup
  - optionally performs the first foundation apply
  - optionally builds and pushes the bootstrap fixture image
  - optionally performs the second apply and fetches the service URL
  - optionally verifies the public fixture response
  - optionally destroys isolated integration resources explicitly

Environment overrides:
  AWS_INTEGRATION_RUN_ID       Override the generated run id
  AWS_INTEGRATION_WORKDIR      Reuse a specific working directory
  AWS_INTEGRATION_KEEP_WORKDIR Keep the workdir after exit when set to 1
  AWS_INTEGRATION_AUTO_APPROVE Set to 0 to omit -auto-approve on apply
  AWS_INTEGRATION_AWS_ACCOUNT_ID
                               Override the AWS account id instead of querying STS
  AWS_INTEGRATION_CLEANUP_TIMEOUT_SECONDS
                               Timeout in seconds for cleanup destroy (default: 300)
  AWS_INTEGRATION_SIMULATE_FAILURE_AT
                               Comma-separated step ids to fail locally:
                               config-materialization,first-tofu-apply,
                               bootstrap-image-publish,second-tofu-apply,
                               url-fetch,verification,destroy
  AWS_INTEGRATION_VERIFY_PATH  HTTP path to verify (default: /)
"""

    def colorize_step_label(self, step: str, color: str | None = None) -> str:
        label = f"[{step}]"
        if not color or not self.enable_color:
            return label
        return f"{color}{label}{COLOR_RESET}"

    def emit_step_status(self, step: str, message: str, color: str | None = None) -> None:
        print(f"==> {self.colorize_step_label(step, color)} {message}", file=sys.stderr)

    def log_step(self, step: str, message: str, color: str | None = None) -> None:
        self.current_step = step
        print("", file=sys.stderr)
        self.emit_step_status(step, message, color)

    def note(self, message: str) -> None:
        print(f"-- {self.current_step}: {message}", file=sys.stderr)

    def fail_if_simulated(self, step: str) -> None:
        if step in self.simulated_failure_steps:
            raise RunnerError(f"Simulated failure at step {step}", 97)

    def require_command(self, name: str) -> None:
        if shutil.which(name) is None:
            raise RunnerError(f"Required command not found: {name}")

    def validate_optional_env(self, name: str, value: str | None, pattern: str) -> None:
        if value and re.fullmatch(pattern, value) is None:
            raise RunnerError(f"Invalid value for {name}: {value}")

    def normalize_service_url(self, value: str) -> str:
        candidate = value.strip()
        if not candidate or candidate == "None":
            return ""
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", candidate):
            return candidate
        return f"https://{candidate}"

    def require_materialized_value(self, name: str, value: str | None) -> None:
        if not value or value.startswith("__SET_"):
            raise RunnerError(f"Missing required integration input: {name}")

    def prepare_workdir(self) -> None:
        self.log_step("config-materialization", "Preparing isolated integration workspace")
        if self.workdir:
            self.workdir.mkdir(parents=True, exist_ok=True)
            return

        self.workdir = Path(tempfile.mkdtemp(prefix=f"{self.repo_name}-{self.run_id}-"))
        self.workdir_created = True

    def cleanup_workdir(self, exit_code: int) -> None:
        if not self.workdir:
            return
        if not self.workdir_created:
            print(f"Leaving user-supplied integration workdir in place: {self.workdir}")
            return
        if exit_code != 0:
            print(f"Preserving generated integration workdir after failure: {self.workdir}")
            return
        if self.mode == "plan":
            print(f"Preserving generated integration workdir after plan: {self.workdir}")
            return
        if self.keep_workdir == "1":
            print(f"Keeping integration workdir: {self.workdir}")
            return
        shutil.rmtree(self.workdir, ignore_errors=True)

    def run_with_timeout(
        self,
        timeout_seconds: int,
        logfile: Path,
        cmd: list[str],
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> int:
        with logfile.open("a", encoding="utf-8") as handle:
            try:
                completed = subprocess.run(
                    cmd,
                    cwd=cwd,
                    env=env,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    check=False,
                    text=True,
                    timeout=timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                print(f"Timed out after {timeout_seconds}s: {' '.join(cmd)}", file=handle)
                return 124
        return completed.returncode

    def run_cmd(
        self,
        cmd: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        capture_output: bool = False,
        check: bool = True,
        text: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            capture_output=capture_output,
            check=False,
            text=text,
        )
        if check and completed.returncode != 0:
            stderr = completed.stderr.strip() if completed.stderr else ""
            stdout = completed.stdout.strip() if completed.stdout else ""
            detail = stderr or stdout or f"Command failed: {' '.join(cmd)}"
            raise RunnerError(detail, completed.returncode)
        return completed

    def resolve_github_repo_value(self) -> str:
        if self.github_repo_value:
            return self.github_repo_value
        if os.environ.get("GITHUB_REPO"):
            self.github_repo_value = os.environ["GITHUB_REPO"]
            return self.github_repo_value

        try:
            completed = subprocess.run(
                ["git", "-C", str(self.root_dir), "remote", "get-url", "origin"],
                capture_output=True,
                check=False,
                text=True,
            )
        except FileNotFoundError as exc:
            raise RunnerError(
                "missing: GitHub repo for integration runs is not configured (set GITHUB_REPO or ensure git origin points at the target repository)"
            ) from exc
        if completed.returncode != 0:
            raise RunnerError("missing: GitHub repo for integration runs is not configured (set GITHUB_REPO or ensure git origin points at the target repository)")
        origin_url = completed.stdout.strip()
        if origin_url.endswith(".git"):
            origin_url = origin_url[:-4]
        derived = origin_url.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
        if not derived or derived == origin_url:
            raise RunnerError("missing: GitHub repo for integration runs is not configured (set GITHUB_REPO or ensure git origin points at the target repository)")
        self.github_repo_value = derived
        return derived

    def check_tool(self, tool: str) -> bool:
        if shutil.which(tool):
            print(f"ready: tool '{tool}' is installed")
            return True
        print(f"missing: tool '{tool}' is not installed")
        return False

    def check_env_value(self, name: str) -> bool:
        if os.environ.get(name):
            print(f"ready: {name} is set")
            return True
        print(f"missing: {name} is not set")
        return False

    def check_aws_credentials_source(self) -> bool:
        if os.environ.get("AWS_PROFILE"):
            print("ready: AWS credentials source is configured via AWS_PROFILE")
            return True
        if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"):
            print("ready: AWS credentials source is configured via AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY")
            return True
        print("missing: AWS credentials source is not configured (set AWS_PROFILE or AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY)")
        return False

    def check_github_auth_from_prod_tfvars(self) -> bool:
        try:
            value = self.read_github_token_from_prod_tfvars()
        except RunnerError as exc:
            print(f"missing: {exc.message}")
            return False
        if value:
            print(f"ready: GitHub provider auth is configured via {self.prod_tfvars_path()}")
            return True
        print(f"missing: GitHub provider auth is not configured (add github_token to {self.prod_tfvars_path()})")
        return False

    def check_github_repo_target(self) -> bool:
        try:
            value = self.resolve_github_repo_value()
        except RunnerError as exc:
            print(exc.message)
            return False
        if os.environ.get("GITHUB_REPO"):
            print(f"ready: GitHub repo for integration runs is {value} (via GITHUB_REPO)")
        else:
            print(f"ready: GitHub repo for integration runs is {value} (derived from git origin)")
        return True

    def run_preflight(self) -> int:
        self.log_step("preflight", "Checking local readiness for the first real AWS integration run")
        failures = 0
        for tool in ("tofu", "aws", "docker", "jq", "python3"):
            if not self.check_tool(tool):
                failures += 1
        if os.environ.get("GITHUB_REPO"):
            print("note: git is not required because GITHUB_REPO is set explicitly")
        elif not self.check_tool("git"):
            failures += 1
        for env_name in ("AWS_REGION", "TF_STATE_BUCKET", "GITHUB_OWNER"):
            if not self.check_env_value(env_name):
                failures += 1
        if not self.check_github_repo_target():
            failures += 1
        if not self.check_aws_credentials_source():
            failures += 1

        prod_tfvars = self.prod_tfvars_path()
        prod_tfvars_exists = prod_tfvars.exists()
        github_auth_from_env = False
        if os.environ.get("TF_VAR_github_token"):
            print("ready: GitHub provider auth is configured via TF_VAR_github_token")
            github_auth_from_env = True
        elif os.environ.get("GITHUB_TOKEN"):
            print("ready: GitHub provider auth is configured via GITHUB_TOKEN")
            github_auth_from_env = True
        if prod_tfvars_exists:
            print(f"ready: {prod_tfvars} exists")
        elif github_auth_from_env:
            print(f"note: {prod_tfvars} does not exist; env-based GitHub auth will be used for isolated Terraform runs")
        else:
            print(f"missing: {prod_tfvars} does not exist")
            failures += 1

        if github_auth_from_env:
            pass
        elif prod_tfvars_exists:
            if not self.check_github_auth_from_prod_tfvars():
                failures += 1
        else:
            print(f"note: GitHub provider auth will be satisfied by setting GITHUB_TOKEN or adding github_token to {prod_tfvars}")

        if failures:
            print(f"Preflight failed with {failures} missing item(s).", file=sys.stderr)
            return 1
        print("Preflight passed: environment is ready for a real AWS integration run.")
        return 0

    def probe_existing_oidc_provider(self) -> ProbeResult:
        payload = json.dumps({"url": "https://token.actions.githubusercontent.com", "audience": "sts.amazonaws.com"})
        completed = subprocess.run(
            ["bash", str(self.root_dir / "scripts" / "check-github-oidc-provider.sh")],
            input=payload,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode == EXIT_OIDC_PROBE_OPTIONAL_FALLBACK:
            message = completed.stderr.strip() or completed.stdout.strip() or "OIDC provider check warning"
            return ProbeResult("warning", message=message)
        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip() or "OIDC provider check failed"
            return ProbeResult("error", message=message)
        try:
            payload_json = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return ProbeResult("error", message="Failed to parse OIDC provider probe result.")
        if payload_json.get("exists") == "true":
            return ProbeResult("found", arn=payload_json.get("arn"))
        return ProbeResult("not-found")

    def read_github_token_from_prod_tfvars(self) -> str | None:
        prod_tfvars = self.prod_tfvars_path()
        if not prod_tfvars.exists():
            return None
        content = prod_tfvars.read_text(encoding="utf-8")
        lines = content.splitlines()

        for index, line in enumerate(lines):
            match = re.match(r"^[ \t]*github_token[ \t]*=[ \t]*(.*)$", line)
            if not match:
                continue

            remainder = match.group(1).strip()
            if not remainder:
                raise RunnerError(f"Failed to parse github_token from {prod_tfvars}: value must be a non-empty string")

            inline_string = self._parse_inline_hcl_string(remainder)
            if inline_string is not None:
                return inline_string

            heredoc_match = re.match(r"<<-?([A-Za-z0-9_]+)$", remainder)
            if heredoc_match:
                delimiter = heredoc_match.group(1)
                return self._parse_heredoc_string(prod_tfvars, lines, index + 1, delimiter)

            raise RunnerError(
                f"Failed to parse github_token from {prod_tfvars}: expected a double-quoted string or heredoc assignment"
            )
        return None

    def _parse_inline_hcl_string(self, value: str) -> str | None:
        if not value.startswith('"'):
            return None

        escaped = False
        closing_index = -1
        for index in range(1, len(value)):
            char = value[index]
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                closing_index = index
                break

        if closing_index == -1:
            raise RunnerError("Failed to parse github_token from tfvars: unterminated double-quoted string")

        trailing = value[closing_index + 1 :].strip()
        if trailing and not trailing.startswith("#") and not trailing.startswith("//"):
            raise RunnerError("Failed to parse github_token from tfvars: unexpected trailing content after string value")

        quoted_value = value[: closing_index + 1]
        try:
            parsed = json.loads(quoted_value)
        except json.JSONDecodeError as exc:
            raise RunnerError(f"Failed to parse github_token from tfvars: {exc}") from exc
        parsed = parsed.strip()
        if not parsed:
            raise RunnerError("Failed to parse github_token from tfvars: value must be a non-empty string")
        return parsed

    def _parse_heredoc_string(self, prod_tfvars: Path, lines: list[str], start_index: int, delimiter: str) -> str:
        heredoc_lines: list[str] = []
        for line in lines[start_index:]:
            if line.strip() == delimiter:
                value = "\n".join(heredoc_lines).strip()
                if not value:
                    raise RunnerError(f"Failed to parse github_token from {prod_tfvars}: value must be a non-empty string")
                return value
            heredoc_lines.append(line)
        raise RunnerError(f"Failed to parse github_token from {prod_tfvars}: missing heredoc terminator {delimiter}")

    def resolve_github_token_value(self) -> str:
        if self.github_token_value:
            return self.github_token_value
        if os.environ.get("TF_VAR_github_token"):
            self.github_token_value = os.environ["TF_VAR_github_token"]
            return self.github_token_value
        if os.environ.get("GITHUB_TOKEN"):
            self.github_token_value = os.environ["GITHUB_TOKEN"]
            return self.github_token_value
        token_from_tfvars = self.read_github_token_from_prod_tfvars()
        if token_from_tfvars:
            self.github_token_value = token_from_tfvars
            return self.github_token_value
        return ""

    def terraform_env(self) -> dict[str, str]:
        env = os.environ.copy()
        token = self.resolve_github_token_value()
        if token:
            env["TF_VAR_github_token"] = token
        return env

    def load_metadata(self) -> dict[str, Any] | None:
        if not self.metadata_path or not self.metadata_path.exists():
            return None
        try:
            data = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RunnerError(f"Failed to load preserved integration settings from {self.metadata_path}: {exc}")
        run_id = data.get("run_id")
        if run_id != self.run_id:
            raise RunnerError(f"Preserved integration metadata run_id {run_id} does not match AWS_INTEGRATION_RUN_ID {self.run_id}.")
        return data

    def update_metadata(self, **updates: Any) -> None:
        if not self.metadata_path:
            return
        metadata: dict[str, Any] = {}
        if self.metadata_path.exists():
            metadata = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        metadata.update(updates)
        self.metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    def materialize_config(self) -> None:
        self.fail_if_simulated("config-materialization")
        assert self.workdir is not None
        self.integration_prefix = self._slugify(f"{self.repo_name}-{self.run_id}")
        self.service_name = self._trim_name(self.integration_prefix)
        self.ecr_repository_name = self.service_name
        self.image_tag = f"integration-{self.run_id}"
        self.state_key = f"{self.repo_name}/integration/{self.run_id}.tfstate"
        self.tfvars_path = self.workdir / "integration.tfvars"
        self.backend_config_path = self.workdir / "backend.hcl"
        self.metadata_path = self.workdir / "integration-metadata.json"

        self.validate_optional_env("AWS_REGION", os.environ.get("AWS_REGION"), r"[a-z]{2}-[a-z0-9-]+-[0-9]+")
        self.validate_optional_env("GITHUB_OWNER", os.environ.get("GITHUB_OWNER"), r"[A-Za-z0-9_.-]+")
        self.validate_optional_env("GITHUB_REPO", os.environ.get("GITHUB_REPO"), r"[A-Za-z0-9_.-]+")
        self.validate_optional_env("TF_STATE_BUCKET", os.environ.get("TF_STATE_BUCKET"), r"[A-Za-z0-9.-]+")

        aws_region_placeholder = os.environ.get("AWS_REGION", "__SET_AWS_REGION__")
        github_owner_placeholder = os.environ.get("GITHUB_OWNER", "__SET_GITHUB_OWNER__")
        tf_state_bucket_placeholder = os.environ.get("TF_STATE_BUCKET", "__SET_TF_STATE_BUCKET__")
        self.github_token_value = self.resolve_github_token_value()

        self.manage_github_oidc_provider = True
        self.github_oidc_provider_arn_value = ""
        metadata = self.load_metadata()
        if metadata:
            self.integration_prefix = metadata.get("integration_prefix") or self.integration_prefix
            self.service_name = metadata.get("service_name") or self.service_name
            self.ecr_repository_name = metadata.get("ecr_repository_name") or self.ecr_repository_name
            self.image_tag = metadata.get("image_tag") or self.image_tag
            self.state_key = metadata.get("state_key") or self.state_key
            repo = metadata.get("github_repo")
            if repo:
                self.github_repo_value = repo
            if "manage_github_oidc_provider" in metadata and metadata["manage_github_oidc_provider"] is not None:
                self.manage_github_oidc_provider = bool(metadata["manage_github_oidc_provider"])
            arn = metadata.get("github_oidc_provider_arn")
            if arn:
                self.github_oidc_provider_arn_value = arn
            if "manage_github_oidc_provider" not in metadata and arn:
                self.manage_github_oidc_provider = False
            service_arn = metadata.get("service_arn")
            if service_arn:
                self.service_arn_value = service_arn

        try:
            github_repo_placeholder = self.resolve_github_repo_value()
            self.validate_optional_env("derived GITHUB_REPO", github_repo_placeholder, r"[A-Za-z0-9_.-]+")
        except RunnerError:
            github_repo_placeholder = "__SET_GITHUB_REPO__"
            self.github_repo_value = ""

        if not metadata and self.mode in {"run", "foundation-apply"}:
            can_probe = (
                aws_region_placeholder != "__SET_AWS_REGION__"
                and tf_state_bucket_placeholder != "__SET_TF_STATE_BUCKET__"
                and github_owner_placeholder != "__SET_GITHUB_OWNER__"
                and github_repo_placeholder != "__SET_GITHUB_REPO__"
            )
            if can_probe:
                probe = self.probe_existing_oidc_provider()
                if probe.status == "found" and probe.arn:
                    self.manage_github_oidc_provider = False
                    self.github_oidc_provider_arn_value = probe.arn
                elif probe.status == "warning":
                    print(f"warning: {probe.message}; falling back to Terraform-managed GitHub OIDC provider", file=sys.stderr)
                elif probe.status == "error":
                    raise RunnerError(probe.message or "OIDC provider check failed")
        elif self.mode == "destroy" and not metadata:
            raise RunnerError(
                "Destroy mode requires AWS_INTEGRATION_WORKDIR pointing at a preserved integration workdir with integration-metadata.json so it can reuse the original GitHub repo and OIDC settings."
            )

        # After metadata and resolve_github_repo_value, ensure placeholder is set for tfvars
        github_repo_placeholder = self.github_repo_value or github_repo_placeholder

        github_oidc_provider_arn_literal = "null"
        if self.github_oidc_provider_arn_value:
            github_oidc_provider_arn_literal = json.dumps(self.github_oidc_provider_arn_value)

        self.tfvars_path.write_text(
            "\n".join(
                [
                    "# Generated by scripts/run-aws-integration.sh",
                    "# This file is intentionally isolated from infra/prod.tfvars.",
                    "",
                    f'aws_region          = "{aws_region_placeholder}"',
                    f'service_name        = "{self.service_name}"',
                    f'ecs_express_image_tag = "{self.image_tag}"',
                    "ecr_force_delete    = true",
                    f'github_owner        = "{github_owner_placeholder}"',
                    f'github_repo         = "{github_repo_placeholder}"',
                    'github_branch       = "main"',
                    f"manage_github_oidc_provider = {'true' if self.manage_github_oidc_provider else 'false'}",
                    f"github_oidc_provider_arn    = {github_oidc_provider_arn_literal}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        self.backend_config_path.write_text(
            "\n".join(
                [
                    "# Generated by scripts/run-aws-integration.sh",
                    f'bucket       = "{tf_state_bucket_placeholder}"',
                    f'key          = "{self.state_key}"',
                    f'region       = "{aws_region_placeholder}"',
                    "use_lockfile = true",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        metadata_payload = {
            "run_id": self.run_id,
            "integration_prefix": self.integration_prefix,
            "service_name": self.service_name,
            "ecr_repository_name": self.ecr_repository_name,
            "image_tag": self.image_tag,
            "state_key": self.state_key,
            "github_repo": github_repo_placeholder,
            "manage_github_oidc_provider": self.manage_github_oidc_provider,
            "github_oidc_provider_arn": self.github_oidc_provider_arn_value or None,
            "service_arn": self.service_arn_value or None,
            "tfvars_path": str(self.tfvars_path),
            "backend_config_path": str(self.backend_config_path),
        }
        self.metadata_path.write_text(json.dumps(metadata_payload, indent=2) + "\n", encoding="utf-8")

        print(f"Run ID: {self.run_id}")
        print(f"Workdir: {self.workdir}")
        print(f"Integration prefix: {self.integration_prefix}")
        print(f"Integration tfvars: {self.tfvars_path}")
        print(f"Integration backend config: {self.backend_config_path}")
        print(f"Integration metadata: {self.metadata_path}")
        print(f"Suggested backend key: {self.state_key}")
        print(f"Derived service name: {self.service_name}")
        print(f"Derived ECR repository name: {self.ecr_repository_name}")
        print(f"Derived GitHub repo: {github_repo_placeholder}")
        print(f"Manage GitHub OIDC provider: {'false' if not self.manage_github_oidc_provider else 'true'}")
        print(f"Expected bootstrap image tag: {self.image_tag}")

    def run_isolated_tofu_init(self) -> None:
        assert self.backend_config_path is not None
        self.run_cmd(
            ["tofu", "init", "-reconfigure", f"-backend-config={self.backend_config_path}"],
            cwd=self.infra_dir,
            env=self.terraform_env(),
        )

    def run_foundation_apply(self) -> None:
        self.require_materialized_value("AWS_REGION", os.environ.get("AWS_REGION"))
        self.require_materialized_value("TF_STATE_BUCKET", os.environ.get("TF_STATE_BUCKET"))
        self.require_materialized_value("GITHUB_OWNER", os.environ.get("GITHUB_OWNER"))
        self.require_materialized_value("GITHUB_REPO", self.github_repo_value)
        assert self.tfvars_path is not None
        self.log_step("first-tofu-apply", "Running isolated foundation apply")
        self.note(f"Infra dir: {self.infra_dir}")
        self.note(f"Backend config: {self.backend_config_path}")
        self.note(f"Vars file: {self.tfvars_path}")
        self.fail_if_simulated("first-tofu-apply")
        self.run_isolated_tofu_init()
        cmd = ["tofu", "apply", f"-var-file={self.tfvars_path}"]
        if os.environ.get("AWS_INTEGRATION_AUTO_APPROVE", "1") != "0":
            cmd.insert(2, "-auto-approve")
        self.run_cmd(cmd, cwd=self.infra_dir, env=self.terraform_env())

    def run_bootstrap_publish(self) -> None:
        self.require_materialized_value("AWS_REGION", os.environ.get("AWS_REGION"))
        self.require_command("aws")
        self.require_command("docker")
        if not (self.fixture_dir / "Dockerfile").exists() or not (self.fixture_dir / "server.py").exists():
            raise RunnerError(f"Integration fixture is missing from {self.fixture_dir}")
        self.log_step("bootstrap-image-publish", "Publishing bootstrap image")
        self.note(f"Fixture dir: {self.fixture_dir}")
        self.note(f"ECR repository: {self.ecr_repository_name}")
        self.fail_if_simulated("bootstrap-image-publish")
        aws_account_id = os.environ.get("AWS_INTEGRATION_AWS_ACCOUNT_ID")
        if not aws_account_id:
            completed = self.run_cmd(["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"], capture_output=True)
            aws_account_id = completed.stdout.strip()
        self.require_materialized_value("AWS account id", aws_account_id)
        registry = f"{aws_account_id}.dkr.ecr.{os.environ['AWS_REGION']}.amazonaws.com"
        self.remote_image_uri = f"{registry}/{self.ecr_repository_name}:{self.image_tag}"
        local_image_tag = f"{self.ecr_repository_name}:{self.image_tag}"
        self.note(f"Remote image URI: {self.remote_image_uri}")
        self.run_cmd(["aws", "ecr", "describe-repositories", "--repository-names", self.ecr_repository_name, "--region", os.environ["AWS_REGION"]])
        password = self.run_cmd(
            ["aws", "ecr", "get-login-password", "--region", os.environ["AWS_REGION"]],
            capture_output=True,
        ).stdout
        login = subprocess.run(
            ["docker", "login", "--username", "AWS", "--password-stdin", registry],
            input=password,
            text=True,
            capture_output=True,
            check=False,
        )
        if login.returncode != 0:
            raise RunnerError((login.stderr or login.stdout).strip() or "docker login failed", login.returncode)
        self.run_cmd(["docker", "build", "-t", local_image_tag, str(self.fixture_dir)])
        self.run_cmd(["docker", "tag", local_image_tag, self.remote_image_uri])
        self.run_cmd(["docker", "push", self.remote_image_uri])

    def fetch_service_url(self) -> str:
        self.log_step("url-fetch", "Resolving ECS Express service URL")
        self.fail_if_simulated("url-fetch")
        assert self.workdir is not None
        tofu_error_log = self.workdir / "tofu-service-url.stderr.log"
        aws_error_log = self.workdir / "aws-service-url.stderr.log"
        tofu_init_failure = ""
        service_url = ""
        service_arn = self.service_arn_value
        try:
            self.run_isolated_tofu_init()
        except RunnerError as exc:
            tofu_init_failure = exc.message
            print(f"-- url-fetch: tofu init failed, falling back to AWS CLI lookup: {exc.message}", file=sys.stderr)
            tofu_error_log.write_text(f"{tofu_init_failure}\n", encoding="utf-8")
        else:
            service_arn_output = subprocess.run(
                ["tofu", "output", "-raw", "ecs_express_service_arn"],
                cwd=self.infra_dir,
                env=self.terraform_env(),
                text=True,
                capture_output=True,
                check=False,
            )
            if service_arn_output.returncode == 0 and service_arn_output.stdout.strip():
                service_arn = service_arn_output.stdout.strip()
                self.service_arn_value = service_arn
                self.update_metadata(service_arn=service_arn)
            tofu_output = subprocess.run(
                ["tofu", "output", "-raw", "service_url"],
                cwd=self.infra_dir,
                env=self.terraform_env(),
                text=True,
                capture_output=True,
                check=False,
            )
            tofu_stderr = tofu_output.stderr or ""
            tofu_error_log.write_text(tofu_stderr, encoding="utf-8")
            service_url = self.normalize_service_url(tofu_output.stdout) if tofu_output.returncode == 0 else ""
            if service_url:
                return service_url

        self.require_command("aws")
        self.require_materialized_value("AWS_REGION", os.environ.get("AWS_REGION"))
        self.require_materialized_value("ECS Express service ARN", service_arn)
        aws_output = subprocess.run(
            [
                "aws",
                "ecs",
                "describe-express-gateway-service",
                "--region",
                os.environ["AWS_REGION"],
                "--service-arn",
                service_arn,
                "--query",
                "service.activeConfigurations[0].ingressPaths[?accessType=='PUBLIC'].endpoint | [0]",
                "--output",
                "text",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        aws_error_log.write_text(aws_output.stderr or "", encoding="utf-8")
        service_url = self.normalize_service_url(aws_output.stdout) if aws_output.returncode == 0 else ""
        if service_url:
            return service_url

        message = "Unable to determine ECS Express service URL after second apply."
        if tofu_error_log.stat().st_size:
            message += f"\ntofu output stderr saved to {tofu_error_log}"
        if aws_error_log.stat().st_size:
            message += f"\naws ecs describe-express-gateway-service stderr saved to {aws_error_log}"
        raise RunnerError(message)

    def run_second_apply(self) -> None:
        self.require_materialized_value("AWS_REGION", os.environ.get("AWS_REGION"))
        self.require_materialized_value("TF_STATE_BUCKET", os.environ.get("TF_STATE_BUCKET"))
        self.require_materialized_value("GITHUB_OWNER", os.environ.get("GITHUB_OWNER"))
        self.require_materialized_value("GITHUB_REPO", self.github_repo_value)
        assert self.tfvars_path is not None
        self.log_step("second-tofu-apply", "Running isolated second apply")
        self.note(f"Infra dir: {self.infra_dir}")
        self.note(f"Backend config: {self.backend_config_path}")
        self.note(f"Vars file: {self.tfvars_path}")
        self.fail_if_simulated("second-tofu-apply")
        self.run_isolated_tofu_init()
        cmd = ["tofu", "apply", f"-var-file={self.tfvars_path}"]
        if os.environ.get("AWS_INTEGRATION_AUTO_APPROVE", "1") != "0":
            cmd.insert(2, "-auto-approve")
        self.run_cmd(cmd, cwd=self.infra_dir, env=self.terraform_env())
        service_url = self.fetch_service_url()
        print(f"Service URL: {service_url}")

    def run_verify(self) -> None:
        self.require_command("python3")
        service_url = self.fetch_service_url()
        self.log_step("verification", "Verifying public fixture response")
        self.fail_if_simulated("verification")
        verify_path = os.environ.get("AWS_INTEGRATION_VERIFY_PATH", "/")
        if not verify_path.startswith("/"):
            verify_path = "/" + verify_path
        assert self.workdir is not None
        self.verify_response_path = self.workdir / "verify-response.json"
        self.note(f"Service URL: {service_url}")
        self.note(f"Verify path: {verify_path}")
        self.note(f"Response capture: {self.verify_response_path}")
        target_url = parse.urljoin(service_url.rstrip("/") + "/", verify_path.lstrip("/"))
        try:
            with request.urlopen(target_url, timeout=30) as response:
                status_code = response.getcode()
                body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            raise RunnerError(f"Fixture verification failed with HTTP {exc.code}: {target_url}")
        except error.URLError as exc:
            raise RunnerError(f"Fixture verification failed to reach {target_url}: {exc}")
        if status_code != 200:
            raise RunnerError(f"Fixture verification returned unexpected status {status_code}: {target_url}")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RunnerError(f"Fixture verification returned invalid JSON from {target_url}: {exc}")
        if payload.get("status") != "ok":
            raise RunnerError(f"Fixture verification expected status='ok' but got {payload.get('status')!r}")
        if payload.get("service") != "minimal-aws-github-ci-template":
            raise RunnerError(
                f"Fixture verification expected service='minimal-aws-github-ci-template' but got {payload.get('service')!r}"
            )
        if payload.get("path") != verify_path:
            raise RunnerError(f"Fixture verification expected path={verify_path!r} but got {payload.get('path')!r}")
        self.verify_response_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.emit_step_status("verification", f"Fixture verification passed: {target_url}", COLOR_GREEN)

    def run_destroy(self, destroy_reason: str) -> None:
        self.require_materialized_value("AWS_REGION", os.environ.get("AWS_REGION"))
        self.require_materialized_value("TF_STATE_BUCKET", os.environ.get("TF_STATE_BUCKET"))
        self.require_materialized_value("GITHUB_OWNER", os.environ.get("GITHUB_OWNER"))
        self.require_materialized_value("GITHUB_REPO", self.github_repo_value)
        assert self.workdir is not None
        assert self.backend_config_path is not None
        assert self.tfvars_path is not None
        self.log_step("destroy", f"Running isolated destroy ({destroy_reason})")
        self.note(f"Run id: {self.run_id}")
        self.note(f"Infra dir: {self.infra_dir}")
        self.note(f"Backend config: {self.backend_config_path}")
        self.note(f"Vars file: {self.tfvars_path}")
        if destroy_reason == "manual" and not self.run_id_explicit:
            raise RunnerError("Manual destroy requires an explicit AWS_INTEGRATION_RUN_ID so the runner does not guess which isolated stack to tear down.")
        self.fail_if_simulated("destroy")
        self.tofu_destroy_log_path = self.workdir / ("cleanup-destroy.log" if destroy_reason == "failure-cleanup" else "destroy.log")

        auto_approve = os.environ.get("AWS_INTEGRATION_AUTO_APPROVE", "1") != "0"
        with self.tofu_destroy_log_path.open("a", encoding="utf-8") as log_handle:
            init_cmd = [
                "tofu",
                "init",
                "-reconfigure",
                f"-backend-config={self.backend_config_path}",
            ]
            init_proc = subprocess.run(
                init_cmd,
                cwd=self.infra_dir,
                env=self.terraform_env(),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
            if init_proc.returncode != 0:
                raise RunnerError(f"Tofu init for destroy failed with exit code {init_proc.returncode}", init_proc.returncode)

            destroy_cmd = [
                "tofu",
                "destroy",
                f"-var-file={self.tfvars_path}",
            ]
            if auto_approve:
                destroy_cmd.append("-auto-approve")

            rc = self.run_with_timeout(
                self.cleanup_timeout_seconds,
                self.tofu_destroy_log_path,
                destroy_cmd,
                cwd=self.infra_dir,
                env=self.terraform_env(),
            )
            if rc != 0:
                raise RunnerError(f"Destroy failed with exit code {rc}", rc)

        self.note(f"Destroy log: {self.tofu_destroy_log_path}")
        self.emit_step_status("destroy", "Destroy succeeded.", COLOR_GREEN)

    def attempt_cleanup_destroy(self) -> int:
        if not self.cleanup_required:
            return 0
        self.cleanup_attempted = True
        self.log_step("destroy", "Attempting failure cleanup with isolated destroy", COLOR_RED)
        for name in ("AWS_REGION", "TF_STATE_BUCKET", "GITHUB_OWNER"):
            value = os.environ.get(name)
            if not value or value.startswith("__SET_"):
                raise CleanupSkipped(f"Cleanup destroy skipped: {name} is not materialized.")
        if not self.github_repo_value or self.github_repo_value.startswith("__SET_"):
            raise CleanupSkipped("Cleanup destroy skipped: GITHUB_REPO is not materialized.")
        if "destroy" in self.simulated_failure_steps:
            raise RunnerError("Simulated failure at step destroy", 97)
        try:
            self.run_destroy("failure-cleanup")
            return 0
        except RunnerError as exc:
            return exc.exit_code

    def write_cleanup_summary(self, cleanup_status: str) -> None:
        if not self.workdir or not self.workdir.exists():
            return
        self.cleanup_status_path = self.workdir / "cleanup-status.json"
        payload = {
            "run_id": self.run_id,
            "workdir": str(self.workdir),
            "primary_step": self.primary_failure_step,
            "primary_exit_code": self.original_exit_code,
            "cleanup_attempted": self.cleanup_attempted,
            "cleanup_status": cleanup_status,
            "cleanup_exit_code": self.cleanup_exit_code,
            "state_key": self.state_key,
            "tfvars_path": str(self.tfvars_path) if self.tfvars_path else "",
            "backend_config_path": str(self.backend_config_path) if self.backend_config_path else "",
            "destroy_log_path": str(self.tofu_destroy_log_path) if self.tofu_destroy_log_path else "",
        }
        self.cleanup_status_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def manual_destroy_command(self) -> str:
        command = f"AWS_INTEGRATION_RUN_ID={shlex.quote(self.run_id)}"
        if self.workdir:
            command += f" AWS_INTEGRATION_WORKDIR={shlex.quote(str(self.workdir))}"
        command += " ./scripts/run-aws-integration.sh destroy"
        return command

    def print_plan(self) -> None:
        assert self.backend_config_path is not None
        assert self.tfvars_path is not None
        print(
            f"""

Planned AWS integration sequence
0. Run the end-to-end integration sequence with failure cleanup:
   ./scripts/run-aws-integration.sh run

Readiness check before any AWS call:
   ./scripts/run-aws-integration.sh preflight

1. Initialize OpenTofu with an isolated backend key:
   cd "{self.infra_dir}"
   tofu init -reconfigure -backend-config="{self.backend_config_path}"

2. Run the first foundation apply with isolated vars:
   tofu apply -var-file="{self.tfvars_path}"

3. Publish the bootstrap image to ECR repository {self.ecr_repository_name} using tag {self.image_tag}.
   ./scripts/run-aws-integration.sh bootstrap-publish

4. Run the second apply so Terraform can create the ECS Express service:
   ./scripts/run-aws-integration.sh second-apply

5. Fetch the ECS Express service URL and verify the public fixture response.
   ./scripts/run-aws-integration.sh verify

6. Destroy the isolated integration stack and remove temp artifacts:
   The runner now destroys automatically at the end of a successful run.
   It also attempts destroy automatically on failure, bounded by a timeout.

7. Manually destroy a prior run by reusing the same isolated run id:
   AWS_INTEGRATION_RUN_ID=<previous-run-id> \\
   AWS_INTEGRATION_WORKDIR=/path/to/preserved-workdir \\
   ./scripts/run-aws-integration.sh destroy
"""
        )

    def finalize_run(self, exit_code: int) -> None:
        cleanup_status = "not-needed"
        if exit_code != 0:
            self.primary_failure_step = self.current_step
            self.emit_step_status(self.current_step, f"Integration runner failed with exit code {exit_code}.", COLOR_RED)
            self.original_exit_code = exit_code
            if self.cleanup_required:
                try:
                    self.cleanup_exit_code = self.attempt_cleanup_destroy()
                except CleanupSkipped as exc:
                    self.cleanup_exit_code = EXIT_CLEANUP_SKIPPED
                    cleanup_status = "skipped"
                    self.emit_step_status("destroy", exc.message, COLOR_YELLOW)
                    print("Cleanup skipped during step 'destroy' because required integration inputs were not materialized.", file=sys.stderr)
                except RunnerError as exc:
                    self.cleanup_exit_code = exc.exit_code
                    cleanup_status = "failed"
                    self.emit_step_status("destroy", f"Cleanup also failed with exit code {exc.exit_code}.", COLOR_RED)
                    print(f"Manual cleanup command: {self.manual_destroy_command()}", file=sys.stderr)
                    if self.tofu_destroy_log_path and self.tofu_destroy_log_path.exists():
                        print(f"Cleanup logs saved to {self.tofu_destroy_log_path}", file=sys.stderr)
                else:
                    if self.cleanup_exit_code == 0:
                        cleanup_status = "succeeded"
                        self.emit_step_status("destroy", "Cleanup succeeded.", COLOR_GREEN)
                    else:
                        cleanup_status = "failed"
                        self.emit_step_status("destroy", f"Cleanup also failed with exit code {self.cleanup_exit_code}.", COLOR_RED)
                        print(f"Manual cleanup command: {self.manual_destroy_command()}", file=sys.stderr)
                        if self.tofu_destroy_log_path and self.tofu_destroy_log_path.exists():
                            print(f"Cleanup logs saved to {self.tofu_destroy_log_path}", file=sys.stderr)
            self.write_cleanup_summary(cleanup_status)
            if self.cleanup_status_path and self.cleanup_status_path.exists():
                print(f"Cleanup summary saved to {self.cleanup_status_path}", file=sys.stderr)
        self.cleanup_workdir(exit_code)

    def run_full_sequence(self) -> None:
        self.run_foundation_apply()
        self.run_bootstrap_publish()
        self.run_second_apply()
        self.run_verify()
        self.run_destroy("success")

    def main(self) -> int:
        if self.mode in {"-h", "--help"}:
            print(self.usage(), end="")
            return 0
        if self.mode not in VALID_MODES:
            print(f"Unsupported mode: {self.mode}", file=sys.stderr)
            print(self.usage(), file=sys.stderr, end="")
            return 1
        if self.mode == "destroy" and not self.run_id_explicit:
            print("Destroy mode requires AWS_INTEGRATION_RUN_ID so the runner does not guess which isolated stack to tear down.", file=sys.stderr)
            return 1
        if self.mode == "preflight":
            return self.run_preflight()

        required_commands = ["tofu", "jq", "mktemp"]
        for command in required_commands:
            self.require_command(command)

        self.prepare_workdir()
        self.materialize_config()
        if self.mode != "plan":
            self.cleanup_required = True

        if self.mode == "plan":
            self.print_plan()
            return 0
        if self.mode == "run":
            self.run_full_sequence()
            self.emit_step_status("run", "Integration run completed successfully.", COLOR_GREEN)
            return 0
        if self.mode == "foundation-apply":
            self.run_foundation_apply()
            return 0
        if self.mode == "bootstrap-publish":
            self.run_bootstrap_publish()
            return 0
        if self.mode == "second-apply":
            self.run_second_apply()
            return 0
        if self.mode == "verify":
            self.run_verify()
            return 0
        self.run_destroy("manual")
        return 0


def main(argv: list[str]) -> int:
    runner = AwsIntegrationRunner(argv)
    exit_code = 0
    try:
        exit_code = runner.main()
    except RunnerError as exc:
        print(exc.message, file=sys.stderr)
        exit_code = exc.exit_code
    except Exception as exc:
        print(f"Unexpected error: {exc}", file=sys.stderr)
        exit_code = 1
    finally:
        runner.finalize_run(exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
