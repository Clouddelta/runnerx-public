"""Exercise release failure gates using fake CLIs; never contact a cloud API."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


FAKE_CLI = r'''#!/usr/bin/env python
import json
import os
from pathlib import Path
import sys

tool = Path(sys.argv[0]).name
arguments = sys.argv[1:]
with open(os.environ["RELEASE_TEST_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps([tool, *arguments]) + "\n")
scenario = os.environ["RELEASE_TEST_SCENARIO"]
if tool == "gcloud":
    if arguments[:3] == ["run", "services", "list"]:
        if scenario != "first_release":
            print("runnerx-api")
    elif arguments[:3] == ["run", "services", "describe"]:
        print(json.dumps({"status": {
            "url": "https://runnerx.example.invalid",
            "traffic": [
                {"revisionName": "runnerx-api-old", "percent": 100},
                {"revisionName": "runnerx-api-r123-1", "tag": "candidate",
                 "url": "https://candidate.example.invalid"}
            ]
        }}))
    elif arguments[:3] == ["run", "jobs", "deploy"]:
        if scenario == "migration_failure":
            sys.exit(7)
    elif arguments[:2] == ["auth", "print-identity-token"]:
        print("fake-test-token")
elif tool == "curl" and scenario == "readiness_failure":
    sys.exit(22)
'''


class ReleaseGateTests(unittest.TestCase):
    def run_scenario(self, scenario):
        bash = os.environ.get("RUNNERX_BASH") or shutil.which("bash")
        self.assertIsNotNone(bash, "Bash is required for deployment flow tests")
        script = Path(__file__).with_name("deploy.sh").resolve()
        with tempfile.TemporaryDirectory(prefix="runnerx-release-test-") as directory:
            temporary = Path(directory)
            commands = temporary / "bin"
            commands.mkdir()
            for tool in ("gcloud", "docker", "curl"):
                executable = commands / tool
                executable.write_text(FAKE_CLI, encoding="utf-8", newline="\n")
                executable.chmod(0o755)
            log = temporary / "commands.jsonl"
            environment = os.environ | {
                "RELEASE_FAKE_BIN": str(commands),
                "RELEASE_TEST_LOG": str(log),
                "RELEASE_TEST_SCENARIO": scenario,
                "GCP_PROJECT_ID": "example-project",
                "GCP_REGION": "us-central1",
                "ARTIFACT_REPOSITORY": "runnerx-images",
                "CLOUD_RUN_SERVICE": "runnerx-api",
                "CLOUD_SQL_CONNECTION_NAME": "example-project:us-central1:runnerx-mysql",
                "RUNTIME_SERVICE_ACCOUNT": "runtime@example.invalid",
                "DEPLOY_SERVICE_ACCOUNT": "deployer@example.invalid",
                "DB_NAME": "runnerx",
                "DB_USER": "runnerx",
                "DB_PASSWORD_SECRET": "runnerx-database-password",
                "DB_PASSWORD_SECRET_VERSION": "1",
                "GITHUB_RUN_ID": "123",
                "GITHUB_RUN_ATTEMPT": "1",
            }
            # Functions override real executables even if Git Bash prepends its
            # own bin directory to PATH on Windows. No real cloud/network CLI
            # is reachable through these names during the sourced script.
            launcher = "\n".join([
                'gcloud() { python "$RELEASE_FAKE_BIN/gcloud" "$@"; }',
                'docker() { python "$RELEASE_FAKE_BIN/docker" "$@"; }',
                'curl() { python "$RELEASE_FAKE_BIN/curl" "$@"; }',
                'source "$1"',
            ])
            result = subprocess.run(
                [bash, "-c", launcher, "--", str(script)], env=environment, capture_output=True, text=True,
                timeout=30, check=False,
            )
            calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return result, calls

    def test_migration_failure_prevents_service_deployment(self):
        result, calls = self.run_scenario("migration_failure")
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertFalse(any(call[:3] == ["gcloud", "run", "deploy"] for call in calls))
        self.assertFalse(any("update-traffic" in call for call in calls))

    def test_readiness_failure_preserves_previous_traffic(self):
        result, calls = self.run_scenario("readiness_failure")
        self.assertEqual(result.returncode, 22, result.stderr)
        deployment = next(call for call in calls if call[:3] == ["gcloud", "run", "deploy"])
        self.assertIn("--no-traffic", deployment)
        self.assertFalse(any("update-traffic" in call for call in calls))

    def test_success_releases_the_tested_revision(self):
        result, calls = self.run_scenario("success")
        self.assertEqual(result.returncode, 0, result.stderr)
        readiness_index = next(index for index, call in enumerate(calls) if call[0] == "curl")
        release_index = next(index for index, call in enumerate(calls) if "update-traffic" in call)
        self.assertLess(readiness_index, release_index)
        self.assertIn("--to-revisions=runnerx-api-r123-1=100", calls[release_index])

    def test_first_release_has_no_previous_traffic(self):
        result, calls = self.run_scenario("first_release")
        self.assertEqual(result.returncode, 0, result.stderr)
        deployment = next(call for call in calls if call[:3] == ["gcloud", "run", "deploy"])
        self.assertNotIn("--no-traffic", deployment)
        self.assertIn("--no-allow-unauthenticated", deployment)


if __name__ == "__main__":
    unittest.main()
