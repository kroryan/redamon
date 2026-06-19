"""Unit tests for the garak subprocess runner (the previously-mocked layer).

Asserts the exact CLI we build (TOOL_API.md §1), the judge/key env wiring, the
report.jsonl location logic, and timeout handling. subprocess is mocked.
"""
import os
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from adapters.garak import runner
from adapters.garak.runner import _locate_report, run_garak_scan


class TestCommandConstruction(unittest.TestCase):
    def test_cmd_flags_and_env(self):
        with patch.object(runner.subprocess, "run") as mrun:
            mrun.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            report, rc, tail = run_garak_scan(
                config_path="/cfg.json", probes=["dan", "encoding"],
                generations=3, seed=7, report_prefix="/out/garak_run",
                judge_base_url="http://localhost:11434", api_key="secret")
        cmd = mrun.call_args.args[0]
        # cmd[0] is the garak venv interpreter (GARAK_PYTHON), "python" in dev.
        self.assertEqual(cmd[0], runner.GARAK_PYTHON)
        self.assertEqual(cmd[1:3], ["-m", "garak"])
        self.assertEqual(cmd[cmd.index("--model_type") + 1], "rest")
        self.assertEqual(cmd[cmd.index("--generator_option_file") + 1], "/cfg.json")
        self.assertEqual(cmd[cmd.index("--probes") + 1], "dan,encoding")
        self.assertEqual(cmd[cmd.index("--generations") + 1], "3")
        self.assertEqual(cmd[cmd.index("--seed") + 1], "7")
        self.assertEqual(cmd[cmd.index("--report_prefix") + 1], "/out/garak_run")
        env = mrun.call_args.kwargs["env"]
        self.assertEqual(env["REST_API_KEY"], "secret")
        self.assertTrue(env["OPENAI_API_BASE"].endswith("/v1"))
        self.assertEqual(rc, 0)

    def test_no_key_no_rest_api_key_env(self):
        with patch.object(runner.subprocess, "run") as mrun:
            mrun.return_value = MagicMock(returncode=0, stdout="", stderr="")
            run_garak_scan("/c.json", ["dan"], 1, 0, "/out/g")
        env = mrun.call_args.kwargs["env"]
        self.assertNotIn("REST_API_KEY", env)
        self.assertNotIn("OPENAI_API_BASE", env)  # no judge_base_url given

    def test_timeout_returns_rc_minus_one(self):
        with patch.object(runner.subprocess, "run",
                          side_effect=subprocess.TimeoutExpired(cmd="garak", timeout=1)):
            report, rc, tail = run_garak_scan("/c.json", ["dan"], 1, 0, "/out/g", timeout=1)
        self.assertEqual(rc, -1)
        self.assertIn("TIMEOUT", tail)


class TestLocateReport(unittest.TestCase):
    def test_exact_path(self):
        with tempfile.TemporaryDirectory() as d:
            prefix = os.path.join(d, "garak_run")
            exact = prefix + ".report.jsonl"
            open(exact, "w").close()
            self.assertEqual(_locate_report(prefix), exact)

    def test_glob_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            other = os.path.join(d, "garak.abc123.report.jsonl")
            open(other, "w").close()
            # exact <prefix>.report.jsonl missing -> glob finds the other one
            self.assertEqual(_locate_report(os.path.join(d, "garak_run")), other)

    def test_none_when_absent(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(_locate_report(os.path.join(d, "garak_run")))

    def test_run_returns_none_report_when_no_file(self):
        with tempfile.TemporaryDirectory() as d, \
             patch.object(runner.subprocess, "run") as mrun:
            mrun.return_value = MagicMock(returncode=0, stdout="", stderr="")
            report, rc, tail = run_garak_scan("/c.json", ["dan"], 1, 0,
                                              os.path.join(d, "garak_run"))
        self.assertIsNone(report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
