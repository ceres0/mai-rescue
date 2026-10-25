"""Protocol contract tests using fake HTTP only; no account or token required."""

from email.message import Message
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mai_rescue.mcp import ENDPOINT, McpClient, McpError, READ_ONLY_TOOLS


TOKEN = "synthetic-test-token"
TOOL = {
    "name": "query-my-coupons",
    "inputSchema": {"type": "object", "properties": {}},
}
INITIALIZED = {
    "protocolVersion": "2025-06-18",
    "capabilities": {"tools": {}},
    "serverInfo": {"name": "fake-official", "version": "test"},
}


class FakeResponse(io.BytesIO):
    def __init__(self, body=b"", *, status=200, content_type="application/json", headers=None):
        super().__init__(body)
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        for key, value in (headers or {}).items():
            self.headers[key] = value


class NeverEndingResponse(FakeResponse):
    """Fail if the client waits for EOF after it already has the matched frame."""

    def read1(self, size=-1):
        if self.tell() == len(self.getvalue()):
            raise AssertionError("Client waited for SSE stream closure")
        return super().read1(size)


class FakeOpener:
    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls = []

    def __call__(self, request, *, timeout):
        body = json.loads(request.data)
        self.calls.append((request, body, timeout))
        if not self.steps:
            raise AssertionError("Unexpected network request")
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        if callable(step):
            return step(request, body)
        return step


def rpc_result(result, *, headers=None):
    def respond(request, body):
        return FakeResponse(
            json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}).encode(),
            headers=headers,
        )
    return respond


def boot(*tail, result=None, session="test-session"):
    headers = {"Mcp-Session-Id": session} if session else {}
    return FakeOpener(
        rpc_result(result or INITIALIZED, headers=headers),
        FakeResponse(status=202),
        *tail,
    )


class McpClientTests(unittest.TestCase):
    def test_requires_nonempty_token_and_positive_timeout(self):
        for token in ("", " ", None, "bad\nheader", "bad token"):
            with self.subTest(token=token), self.assertRaises(McpError):
                McpClient(token)
        for timeout in (0, -1, float("nan"), True, "20"):
            with self.subTest(timeout=timeout), self.assertRaises(McpError):
                McpClient(TOKEN, timeout=timeout)

    def test_initialization_headers_session_and_notification(self):
        opener = boot(rpc_result({"tools": [TOOL]}))
        client = McpClient(TOKEN, opener=opener, timeout=7)
        self.assertEqual(client.initialize(), INITIALIZED)
        self.assertEqual(client.initialize(), INITIALIZED)
        self.assertEqual(len(opener.calls), 2)
        self.assertEqual(client.list_tools(), [TOOL])
        initial_request, initial, timeout = opener.calls[0]
        self.assertEqual(initial_request.full_url, ENDPOINT)
        self.assertEqual(initial_request.method, "POST")
        self.assertEqual(timeout, 7)
        self.assertEqual(initial["params"]["protocolVersion"], "2025-06-18")
        self.assertEqual(initial["params"]["capabilities"], {})
        initial_headers = {key.lower(): value for key, value in initial_request.header_items()}
        self.assertEqual(initial_headers["authorization"], f"Bearer {TOKEN}")
        self.assertEqual(initial_headers["accept"], "application/json, text/event-stream")
        self.assertEqual(initial_headers["content-type"], "application/json")
        self.assertNotIn("mcp-session-id", initial_headers)
        self.assertEqual(opener.calls[1][1], {"jsonrpc": "2.0", "method": "notifications/initialized"})
        for request, _, _ in opener.calls[1:]:
            headers = {key.lower(): value for key, value in request.header_items()}
            self.assertEqual(headers["mcp-session-id"], "test-session")
            self.assertEqual(headers["mcp-protocol-version"], "2025-06-18")

    def test_accepts_supported_negotiated_versions(self):
        for version in ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"):
            result = dict(INITIALIZED, protocolVersion=version)
            opener = boot(result=result, session=None)
            self.assertEqual(McpClient(TOKEN, opener=opener).initialize()["protocolVersion"], version)
            headers = {key.lower(): value for key, value in opener.calls[1][0].header_items()}
            self.assertEqual(headers["mcp-protocol-version"], version)
            self.assertNotIn("mcp-session-id", headers)

    def test_rejects_unknown_negotiated_version(self):
        opener = FakeOpener(rpc_result(dict(INITIALIZED, protocolVersion="2099-01-01")))
        with self.assertRaisesRegex(McpError, "协议版本"):
            McpClient(TOKEN, opener=opener).initialize()
        self.assertEqual(len(opener.calls), 1)

    def test_lists_paginated_tools_without_granting_write_permission(self):
        write_tool = {
            "name": "auto-bind-coupons",
            "inputSchema": {"type": "object"},
            "annotations": {"readOnlyHint": True},
        }
        opener = boot(
            rpc_result({"tools": [TOOL], "nextCursor": "page-two"}),
            rpc_result({"tools": [write_tool]}),
        )
        client = McpClient(TOKEN, opener=opener)
        self.assertEqual(client.list_tools(), [TOOL, write_tool])
        self.assertEqual(opener.calls[-1][1]["params"], {"cursor": "page-two"})
        with self.assertRaisesRegex(McpError, "只读"):
            client.call_tool("auto-bind-coupons", {})
        self.assertEqual(len(opener.calls), 4)

    def test_stops_repeated_pagination_cursor(self):
        opener = boot(
            rpc_result({"tools": [], "nextCursor": "same"}),
            rpc_result({"tools": [], "nextCursor": "same"}),
        )
        with self.assertRaisesRegex(McpError, "分页游标"):
            McpClient(TOKEN, opener=opener).list_tools()
        self.assertEqual(len(opener.calls), 4)

    def test_call_returns_unchanged_structured_and_text_result(self):
        result = {
            "content": [{"type": "text", "text": "original account data"}],
            "structuredContent": {"coupons": [{"customField": 1}]},
            "isError": False,
        }
        opener = boot(rpc_result({"tools": [TOOL]}), rpc_result(result))
        client = McpClient(TOKEN, opener=opener)
        self.assertEqual(client.call_tool(TOOL["name"], {}), result)
        request = opener.calls[-1][1]
        self.assertEqual(request["method"], "tools/call")
        self.assertEqual(request["params"], {"name": TOOL["name"], "arguments": {}})

    def test_write_and_unknown_tools_are_refused_before_network(self):
        opener = FakeOpener()
        client = McpClient(TOKEN, opener=opener)
        for name in ("create-order", "mall-create-order", "auto-bind-coupons", "draw-lottery", "unknown", None):
            with self.subTest(name=name), self.assertRaisesRegex(McpError, "只读"):
                client.call_tool(name, {})
        self.assertEqual(opener.calls, [])
        self.assertEqual(len(READ_ONLY_TOOLS), 5)

    def test_allowlisted_tool_must_be_present_on_server(self):
        opener = boot(rpc_result({"tools": []}))
        with self.assertRaisesRegex(McpError, "未提供"):
            McpClient(TOKEN, opener=opener).call_tool(TOOL["name"], {})

    def test_validates_required_nested_fields_and_basic_types(self):
        tool = {
            "name": TOOL["name"],
            "inputSchema": {
                "type": "object",
                "required": ["page", "options"],
                "properties": {
                    "page": {"type": "integer"},
                    "options": {
                        "type": "object",
                        "required": ["tags"],
                        "properties": {"tags": {"type": "array", "items": {"type": "string"}}},
                    },
                },
            },
        }
        opener = boot(rpc_result({"tools": [tool]}))
        client = McpClient(TOKEN, opener=opener)
        invalid = (
            ({}, "必需参数"),
            ({"page": "1", "options": {"tags": []}}, "类型不匹配"),
            ({"page": True, "options": {"tags": []}}, "类型不匹配"),
            ({"page": 1, "options": {}}, "必需参数"),
            ({"page": 1, "options": {"tags": [42]}}, "类型不匹配"),
        )
        for arguments, message in invalid:
            with self.subTest(arguments=arguments), self.assertRaisesRegex(McpError, message):
                client.call_tool(TOOL["name"], arguments)
        self.assertEqual(len(opener.calls), 3)

    def test_tool_execution_errors_are_not_returned_as_success(self):
        opener = boot(
            rpc_result({"tools": [TOOL]}),
            rpc_result({"isError": True, "content": [{"type": "text", "text": f"Bad token: {TOKEN}"}]}),
        )
        with self.assertRaisesRegex(McpError, "工具执行失败") as caught:
            McpClient(TOKEN, opener=opener).call_tool(TOOL["name"], {})
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_jsonrpc_error_redacts_token(self):
        def error_reply(request, body):
            return FakeResponse(json.dumps({
                "jsonrpc": "2.0", "id": body["id"],
                "error": {"code": -32602, "message": f"Invalid arguments {TOKEN}"},
            }).encode())
        with self.assertRaisesRegex(McpError, "-32602") as caught:
            McpClient(TOKEN, opener=FakeOpener(error_reply)).initialize()
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_http_401_429_and_redirect_are_safe_and_not_retried(self):
        for status, expected in ((401, "认证失败"), (429, "过于频繁"), (302, "重定向")):
            error = urllib.error.HTTPError(ENDPOINT, status, TOKEN, {}, io.BytesIO(TOKEN.encode()))
            opener = FakeOpener(error)
            with self.subTest(status=status), self.assertRaisesRegex(McpError, expected) as caught:
                McpClient(TOKEN, opener=opener).initialize()
            self.assertNotIn(TOKEN, str(caught.exception))
            self.assertEqual(len(opener.calls), 1)

    def test_expired_session_404_requires_explicit_reconnection(self):
        opener = boot(urllib.error.HTTPError(ENDPOINT, 404, "gone", {}, io.BytesIO()))
        client = McpClient(TOKEN, opener=opener)
        with self.assertRaisesRegex(McpError, "重新连接"):
            client.list_tools()
        self.assertEqual(len(opener.calls), 3)
        self.assertIsNone(client._session_id)

    def test_default_opener_installs_no_redirect_handler(self):
        captured = []
        class Dummy:
            def open(self, request, *, timeout):
                raise AssertionError("No network in tests")
        def build(*handlers):
            captured.extend(handlers)
            return Dummy()
        with patch("mai_rescue.mcp.urllib.request.build_opener", side_effect=build):
            McpClient(TOKEN)
        handler = next(item for item in captured if isinstance(item, urllib.request.HTTPRedirectHandler))
        self.assertIsNone(handler.redirect_request(None, None, 302, "", {}, "https://other.invalid"))

    def test_sse_multiline_notifications_ids_and_returns_without_eof(self):
        result = {"structuredContent": {"coupons": []}, "content": []}
        def sse_reply(request, body):
            lines = [
                ": heartbeat", "",
                'data: {"jsonrpc":"2.0","method":"notifications/message","params":{}}', "",
                'data: {"jsonrpc":"2.0","id":999,"result":{}}', "",
                'data: {"jsonrpc":"2.0",',
                f'data: "id":{body["id"]},',
                f'data: "result":{json.dumps(result)}}}', "", "",
            ]
            return NeverEndingResponse(("\ufeff" + "\r\n".join(lines)).encode(), content_type="text/event-stream; charset=utf-8")
        opener = boot(rpc_result({"tools": [TOOL]}), sse_reply)
        self.assertEqual(McpClient(TOKEN, opener=opener).call_tool(TOOL["name"], {}), result)

    def test_sse_accepts_bare_cr_lines(self):
        def sse_reply(request, body):
            payload = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": INITIALIZED})
            return FakeResponse((f"data: {payload}\r\r").encode(), content_type="text/event-stream")
        opener = FakeOpener(sse_reply, FakeResponse(status=202))
        self.assertEqual(McpClient(TOKEN, opener=opener).initialize(), INITIALIZED)

    def test_sse_does_not_dispatch_incomplete_frame(self):
        def sse_reply(request, body):
            payload = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": INITIALIZED})
            return FakeResponse(f"data: {payload}\n".encode(), content_type="text/event-stream")
        with self.assertRaisesRegex(McpError, "响应到达前结束"):
            McpClient(TOKEN, opener=FakeOpener(sse_reply)).initialize()

    def test_rejects_wrong_id_and_invalid_response_content(self):
        cases = (
            (b'{"jsonrpc":"2.0","id":999,"result":{}}', "不匹配"),
            (b"not json", "JSON 无法解析"),
            (b'{"jsonrpc":"2.0","id":1,"result":null}', "result"),
            (b'{"jsonrpc":"2.0","id":1,"result":{"value":NaN}}', "JSON 无法解析"),
        )
        for body, expected in cases:
            with self.subTest(body=body), self.assertRaisesRegex(McpError, expected):
                McpClient(TOKEN, opener=FakeOpener(FakeResponse(body))).initialize()

    def test_response_size_limit_for_json_and_sse(self):
        for content_type in ("application/json", "text/event-stream"):
            opener = FakeOpener(FakeResponse(b"x" * 129, content_type=content_type))
            with self.subTest(content_type=content_type), patch("mai_rescue.mcp.MAX_RESPONSE_BYTES", 128):
                with self.assertRaisesRegex(McpError, "8 MB"):
                    McpClient(TOKEN, opener=opener).initialize()

    def test_network_error_does_not_expose_original_message(self):
        opener = FakeOpener(urllib.error.URLError(f"proxy secret {TOKEN}"))
        with self.assertRaisesRegex(McpError, "无法连接") as caught:
            McpClient(TOKEN, opener=opener).initialize()
        self.assertNotIn(TOKEN, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
