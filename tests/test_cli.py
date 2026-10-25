from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mai_rescue.cli import main, token_value, capture_data
from mai_rescue.report import render_html


class CliTests(unittest.TestCase):
    def run_cli(self, args):
        output, error = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            code = main(args)
        return code, output.getvalue(), error.getvalue()

    def test_complete_demo_artifacts_and_budget_behavior(self):
        with tempfile.TemporaryDirectory() as temporary:
            code, _, error = self.run_cli(["demo", "--out", temporary, "--visit", "--budget", "35"])
            self.assertEqual((code, error), (0, ""))
            path = Path(temporary)
            self.assertEqual({p.name for p in path.iterdir()}, {"plan.json", "report.html", "report.md", "reminders.ics"})
            plan = json.loads((path / "plan.json").read_text())
            self.assertEqual(plan["mode"], "demo")
            self.assertEqual(plan["summary"]["max_saving_cents"], 1000)
            self.assertEqual(next(a for a in plan["actions"] if a["id"] == "demo-budget")["decision"], "skip")
            calendar = (path / "reminders.ics").read_bytes()
            self.assertTrue(calendar.endswith(b"END:VCALENDAR\r\n"))
            self.assertNotIn(b"\n", calendar.replace(b"\r\n", b""))
            self.assertIn("合成演示", calendar.decode())
            self.assertIn("DEMO", (path / "report.html").read_text())

    def test_unplanned_demo_never_recommends_spending(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(self.run_cli(["demo", "--out", temporary])[0], 0)
            plan = json.loads((Path(temporary) / "plan.json").read_text())
            self.assertNotIn("consider", [a["decision"] for a in plan["actions"]])
            self.assertEqual(plan["summary"]["max_saving_cents"], 0)

    def test_unknown_horizon_and_naive_now_fail_cleanly(self):
        for options in (["--horizon", "0"], ["--now", "2026-10-25T12:00:00"]):
            with tempfile.TemporaryDirectory() as temporary:
                code, _, error = self.run_cli(["demo", "--out", temporary, *options])
                self.assertEqual(code, 2)
                self.assertTrue(error.startswith("错误："))
                self.assertFalse(list(Path(temporary).iterdir()))

    def test_missing_token_does_not_fall_back_to_demo(self):
        with tempfile.TemporaryDirectory() as temporary, patch("mai_rescue.cli.TOKEN_FILE", Path(temporary) / "missing"), patch.dict("os.environ", {"MCD_MCP_TOKEN": ""}):
            code, output, error = self.run_cli(["discover"])
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertIn("MCD_MCP_TOKEN", error)

    def test_doctor_never_discloses_credentials(self):
        secret = "synthetic-credential-for-offline-test"
        with patch.dict("os.environ", {"MCD_MCP_TOKEN": secret}):
            code, output, error = self.run_cli(["doctor"])
        self.assertEqual(code, 0)
        self.assertNotIn(secret, output + error)
        self.assertIn("未验证有效性", output)

    def test_local_auth_hides_secret_and_restricts_file_permissions(self):
        import os
        secret = "offline-test-local-credential"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".private/mcd-token"
            with patch("mai_rescue.cli.TOKEN_FILE", path), patch("sys.stdin.isatty", return_value=True), patch("getpass.getpass", return_value=secret), patch.dict("os.environ", {"MCD_MCP_TOKEN": ""}):
                code, output, error = self.run_cli(["auth"])
                self.assertEqual(code, 0)
                self.assertNotIn(secret, output + error)
                self.assertEqual(token_value(), secret)
                if os.name == "posix":
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                    path.chmod(0o644)
                    with self.assertRaises(ValueError):
                        token_value()

    def test_auth_requires_interactive_terminal(self):
        with patch("sys.stdin.isatty", return_value=False):
            code, _, error = self.run_cli(["auth"])
        self.assertEqual(code, 2)
        self.assertIn("交互终端", error)

    def test_capture_collects_coupon_pages_and_keeps_page_provenance(self):
        class FakeClient:
            def __init__(self, token):
                self.calls = []
            def list_tools(self):
                return [{"name": n, "inputSchema": {"type": "object"}} for n in
                        ["query-my-coupons", "query-my-account", "campaign-calendar", "available-coupons"]]
            def call_tool(self, name, arguments):
                if name == "query-my-coupons":
                    page = int(arguments.get("page", "1"))
                    return {"structuredContent": {"code": 200, "data": {"currentPage": page, "totalPages": 2, "pageSize": 200, "coupons": []}}}
                return {"structuredContent": {"code": 200, "data": {}}}
        with patch("mai_rescue.mcp.McpClient", FakeClient), patch("mai_rescue.cli.token_value", return_value="offline-only"):
            capture = capture_data({})
        self.assertEqual(len(capture["tool_pages"]["query-my-coupons"]), 2)
        self.assertEqual(capture["tool_results"]["query-my-coupons"]["structuredContent"]["data"]["currentPage"], 1)

    def test_capture_rejects_business_failure_even_without_protocol_error(self):
        class FakeClient:
            def __init__(self, token):
                pass
            def list_tools(self):
                return [{"name": n, "inputSchema": {}} for n in
                        ["query-my-coupons", "query-my-account", "campaign-calendar", "available-coupons"]]
            def call_tool(self, name, arguments):
                return {"structuredContent": {"code": 401, "success": False, "data": {}}}
        with patch("mai_rescue.mcp.McpClient", FakeClient), patch("mai_rescue.cli.token_value", return_value="offline-only"):
            with self.assertRaises(ValueError):
                capture_data({})

    def test_sync_keeps_private_raw_sidecar_for_provenance(self):
        from importlib.resources import files
        snapshot = json.loads(files("mai_rescue").joinpath("demo.json").read_text())
        snapshot["mode"] = "live"
        raw = {"mode": "live", "synthetic_marker": "raw-source"}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "snapshot.json"
            with patch("mai_rescue.cli.capture_data", return_value=raw), patch("mai_rescue.cli.normalize_data", return_value=snapshot):
                code, _, error = self.run_cli(["sync", "--profile", "official-2026-10", "--out", str(path)])
            self.assertEqual((code, error), (0, ""))
            self.assertEqual(json.loads(path.with_name("snapshot.capture.json").read_text()), raw)
            self.assertEqual(json.loads(path.read_text())["mode"], "live")

    def test_report_escapes_account_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.run_cli(["demo", "--out", temporary])
            plan = json.loads((Path(temporary) / "plan.json").read_text())
        plan["actions"][0]["title"] = '<img src=x onerror="alert(1)">'
        plan["actions"][0]["source"]["ref"] = "</script><script>bad()</script>"
        html = render_html(plan)
        self.assertNotIn('<img src=x onerror="alert(1)">', html)
        self.assertNotIn("<script>bad()</script>", html)
        self.assertIn("&lt;img", html)
        self.assertIn("&lt;/script&gt;", html)

    def test_unknown_threshold_remains_unverified_even_without_explicit_budget(self):
        from importlib.resources import files
        from datetime import datetime
        from mai_rescue.engine import build_plan
        snapshot = json.loads(files("mai_rescue").joinpath("demo.json").read_text())
        snapshot["records"] = [snapshot["records"][0]]
        snapshot["records"][0]["min_spend_cents"] = None
        plan = build_plan(snapshot, now=datetime.fromisoformat(snapshot["captured_at"]), planned_visit=True)
        self.assertEqual(plan["actions"][0]["decision"], "check")
        self.assertEqual(plan["summary"]["max_saving_cents"], 0)


if __name__ == "__main__":
    unittest.main()
