import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contentbase.cli import human_confirm, parser, main
from contentbase.errors import BaseError
from contentbase.runner import Runner


class CliTests(unittest.TestCase):
    def test_unsupported_runtime_is_rejected_before_configuration_or_cli_calls(self):
        with patch("contentbase.cli.sys.version_info", (3, 10)), patch("sys.stdout", io.StringIO()) as output:
            with self.assertRaises(SystemExit) as exit_status:
                main(["doctor"])
        self.assertEqual(exit_status.exception.code, 2)
        self.assertEqual(json.loads(output.getvalue())["error"]["code"], "python_version_unsupported")
    def test_clean_config_is_private_and_never_claims_authenticated(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            command = [sys.executable, "-m", "contentbase", "--config", str(path)]
            result = subprocess.run([*command, "init-config"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)
            self.assertFalse(json.loads(result.stdout)["data"]["configured"])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            doctor = subprocess.run([*command, "doctor"], capture_output=True, text=True)
            self.assertEqual(doctor.returncode, 2)
            self.assertEqual(json.loads(doctor.stdout)["error"]["code"], "feishu_binding_required")
            self.assertEqual(subprocess.run([*command, "init-config"], capture_output=True).returncode, 2)

    def test_agent_pipe_does_not_implicitly_approve(self):
        with patch("sys.stdin", io.StringIO("CONFIRM\n")):
            with self.assertRaisesRegex(BaseError, "human_tty_confirmation_required"):
                human_confirm({"data": {"plan": {"fields": {}}}})

    def test_vendor_errors_do_not_echo_credentials_or_command(self):
        response = subprocess.CompletedProcess([], 1, stdout=json.dumps({"ok": False, "error": {"message": "YOUR_SYNTHETIC_CREDENTIAL"}}), stderr="")
        with patch("subprocess.run", return_value=response):
            with self.assertRaises(BaseError) as captured:
                Runner()(["example-cli"], write=True)
        self.assertTrue(captured.exception.unknown)
        self.assertNotIn("YOUR_SYNTHETIC_CREDENTIAL", str(captured.exception))

    def test_protocol_parse_and_no_shell_runner(self):
        self.assertEqual(parser().parse_args(["verify-write", "op_EXAMPLE"]).command, "verify-write")
        response = subprocess.CompletedProcess([], 0, stdout='{"ok":true,"data":{}}', stderr="")
        with patch("subprocess.run", return_value=response) as run:
            Runner()(["example-cli", "argument;not-a-shell"])
        self.assertNotIn("shell", run.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
