"""Small, read-only MCP Streamable HTTP client using Python's standard library.

Input validation deliberately covers only common JSON Schema types and required
properties. The server remains responsible for complete schema validation.
"""

from __future__ import annotations

import codecs
from contextlib import closing
import json
import math
import time
from typing import Any, Callable
import urllib.error
import urllib.request


ENDPOINT = "https://mcp.mcd.cn"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
READ_ONLY_TOOLS = frozenset(
    {
        "query-my-coupons",
        "query-my-account",
        "campaign-calendar",
        "available-coupons",
        "now-time-info",
    }
)
SUPPORTED_PROTOCOL_VERSIONS = frozenset(
    {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}
)


class McpError(RuntimeError):
    """A safe-to-display connection, protocol, validation, or tool error."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A redirected Authorization header must never reach another endpoint.
        return None


def _invalid_constant(value: str) -> None:
    raise ValueError("Non-finite JSON number")


class McpClient:
    """Connect only to the official endpoint; tokens are never put in URLs.

    ``opener`` is an optional urlopen-compatible callable for deterministic tests.
    It must accept a Request and a keyword ``timeout`` and return a binary HTTP
    response. Production callers should leave it unset.
    """

    def __init__(
        self,
        token: str,
        *,
        opener: Callable[..., Any] | None = None,
        timeout: float = 20,
    ) -> None:
        if not isinstance(token, str) or not token.strip():
            raise McpError("请设置非空的 MCP Token。")
        if any(ord(char) < 33 or ord(char) == 127 for char in token.strip()):
            raise McpError("MCP Token 含无效空白或控制字符。")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise McpError("请求超时必须是正数。")
        if not math.isfinite(timeout) or timeout <= 0:
            raise McpError("请求超时必须是正数。")
        self._token = token.strip()
        self._opener = opener or urllib.request.build_opener(_NoRedirect()).open
        self._timeout = timeout
        self._session_id: str | None = None
        self._protocol_version: str | None = None
        self._initialize_result: dict[str, Any] | None = None
        self._tools: list[dict[str, Any]] | None = None
        self._request_id = 0

    def _safe(self, value: Any) -> str:
        return str(value).replace(self._token, "[已隐藏]")[:400]

    def _fail(self, message: str) -> McpError:
        return McpError(self._safe(message))

    def _http_error(self, status: int) -> McpError:
        if status == 401:
            return self._fail("MCP 认证失败（HTTP 401）：请检查 Token 是否有效或已过期。")
        if status == 429:
            return self._fail("MCP 请求过于频繁（HTTP 429）：请稍后再试。")
        if status == 404 and self._session_id:
            self._session_id = None
            self._protocol_version = None
            self._initialize_result = None
            self._tools = None
            return self._fail("MCP 会话已失效（HTTP 404）：请重新连接后再查询。")
        if 300 <= status < 400:
            return self._fail("MCP 返回重定向；已拒绝转发认证信息，请检查官方服务状态。")
        return self._fail(f"MCP HTTP 请求失败（HTTP {status}），请稍后重试。")

    def _parse_json(self, data: bytes | str) -> Any:
        try:
            if isinstance(data, bytes):
                data = data.decode("utf-8-sig")
            return json.loads(data, parse_constant=_invalid_constant)
        except (ValueError, UnicodeError):
            raise self._fail("MCP 返回的 JSON 无法解析。") from None

    def _accept_message(self, message: Any, request_id: int) -> dict[str, Any] | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise self._fail("MCP 返回了无效的 JSON-RPC 消息。")
        if "method" in message:
            if "id" in message:
                reply = {"jsonrpc": "2.0", "id": message["id"]}
                if message["method"] == "ping":
                    reply["result"] = {}
                else:
                    reply["error"] = {"code": -32601, "message": "Method not supported"}
                self._post(reply, notification=True)
            return None
        if type(message.get("id")) is not type(request_id) or message.get("id") != request_id:
            return None
        if "error" in message:
            error = message["error"]
            if isinstance(error, dict):
                raise self._fail(
                    f"MCP 协议错误（{error.get('code', '未知代码')}）：{error.get('message', '请求失败')}"
                )
            raise self._fail("MCP 返回了无效的协议错误。")
        result = message.get("result")
        if not isinstance(result, dict):
            raise self._fail("MCP 响应缺少有效的 result 对象。")
        return result

    def _read_sse(self, response: Any, request_id: int) -> dict[str, Any]:
        # read1 returns available bytes instead of waiting to fill a large read.
        # The one-byte fallback also avoids waiting for the server to close SSE.
        reader = getattr(response, "read1", None)
        if reader is None:
            reader = lambda size: response.read(1)
        decoder = codecs.getincrementaldecoder("utf-8-sig")()
        count = 0
        line: list[str] = []
        data_lines: list[str] = []
        after_cr = False
        deadline = time.monotonic() + self._timeout
        while True:
            if time.monotonic() > deadline:
                raise self._fail("MCP SSE 响应超时，请稍后重试。")
            chunk = reader(min(65536, MAX_RESPONSE_BYTES - count + 1))
            if not chunk:
                break
            count += len(chunk)
            if count > MAX_RESPONSE_BYTES:
                raise self._fail("MCP 响应超过 8 MB，已停止读取。")
            try:
                decoded = decoder.decode(chunk)
            except UnicodeError:
                raise self._fail("MCP SSE 不是有效的 UTF-8。") from None
            for char in decoded:
                if after_cr:
                    after_cr = False
                    if char == "\n":
                        continue
                if char not in "\r\n":
                    line.append(char)
                    continue
                after_cr = char == "\r"
                value = "".join(line)
                line.clear()
                if not value:
                    if data_lines:
                        message = self._parse_json("\n".join(data_lines))
                        data_lines.clear()
                        result = self._accept_message(message, request_id)
                        if result is not None:
                            return result
                elif not value.startswith(":"):
                    field, separator, field_value = value.partition(":")
                    if separator and field_value.startswith(" "):
                        field_value = field_value[1:]
                    if field == "data":
                        data_lines.append(field_value)
        # Per SSE, a partial frame without its final blank line is not dispatched.
        raise self._fail("MCP SSE 在匹配的请求响应到达前结束。")

    def _post(
        self, message: dict[str, Any], *, notification: bool = False
    ) -> tuple[dict[str, Any] | None, Any]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if self._protocol_version:
            headers["MCP-Protocol-Version"] = self._protocol_version
        try:
            body = json.dumps(message, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError):
            raise self._fail("MCP 请求参数必须是有效的 JSON 数据。") from None
        request = urllib.request.Request(ENDPOINT, data=body, headers=headers, method="POST")
        try:
            response = self._opener(request, timeout=self._timeout)
            with closing(response):
                status = getattr(response, "status", None)
                if status is None:
                    status = response.getcode()
                if not 200 <= status < 300:
                    raise self._http_error(status)
                if notification:
                    if status != 202:
                        raise self._fail("MCP 未按协议确认通知（预期 HTTP 202）。")
                    return None, response.headers
                if status == 202:
                    raise self._fail("MCP 未返回当前请求的 JSON-RPC 响应。")
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if content_type == "application/json":
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise self._fail("MCP 响应超过 8 MB，已停止读取。")
                    result = self._accept_message(self._parse_json(raw), message["id"])
                    if result is None:
                        raise self._fail("MCP 响应与当前请求 id 不匹配。")
                    return result, response.headers
                if content_type == "text/event-stream":
                    return self._read_sse(response, message["id"]), response.headers
                raise self._fail("MCP 返回了不支持的响应类型；预期 JSON 或 SSE。")
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
            raise self._http_error(status) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            # Network exception details may include credentials or proxy URLs.
            raise self._fail("无法连接 MCP 服务或请求超时，请检查网络后重试。") from None

    def _rpc(self, method: str, params: dict[str, Any]) -> tuple[dict[str, Any], Any]:
        self._request_id += 1
        result, headers = self._post(
            {"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params}
        )
        assert result is not None
        return result, headers

    def initialize(self) -> dict[str, Any]:
        """Perform version negotiation and the initialized notification once."""
        if self._initialize_result is not None:
            return self._initialize_result
        result, headers = self._rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "mai-rescue", "version": "1.0.0"},
            },
        )
        version = result.get("protocolVersion")
        if not isinstance(version, str) or version not in SUPPORTED_PROTOCOL_VERSIONS:
            raise self._fail("MCP 服务协商了当前客户端不支持的协议版本。")
        if not isinstance(result.get("capabilities"), dict):
            raise self._fail("MCP 初始化响应缺少 capabilities 对象。")
        session = headers.get("Mcp-Session-Id")
        if session is not None and (
            not isinstance(session, str)
            or not session
            or any(not 0x21 <= ord(char) <= 0x7E for char in session)
        ):
            raise self._fail("MCP 返回了无效的会话标识。")
        self._protocol_version = version
        self._session_id = session
        try:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, notification=True)
        except McpError:
            self._protocol_version = None
            self._session_id = None
            raise
        self._initialize_result = result
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        """Discover all tool definitions; discovery grants no call permission."""
        initialization = self.initialize()
        if "tools" not in initialization["capabilities"]:
            raise self._fail("MCP 服务未声明 tools 能力。")
        tools: list[dict[str, Any]] = []
        names: set[str] = set()
        seen_cursors: set[str] = set()
        params: dict[str, Any] = {}
        for _ in range(100):
            result, _ = self._rpc("tools/list", params)
            page = result.get("tools")
            if not isinstance(page, list):
                raise self._fail("MCP 工具列表格式无效。")
            for tool in page:
                if (
                    not isinstance(tool, dict)
                    or not isinstance(tool.get("name"), str)
                    or not tool["name"]
                    or not isinstance(tool.get("inputSchema"), dict)
                ):
                    raise self._fail("MCP 工具定义缺少名称或 inputSchema。")
                if tool["name"] in names:
                    raise self._fail("MCP 工具列表出现重复名称。")
                names.add(tool["name"])
                tools.append(tool)
            cursor = result.get("nextCursor")
            if cursor is None or cursor == "":
                self._tools = tools
                return tools
            if not isinstance(cursor, str) or cursor in seen_cursors:
                raise self._fail("MCP 工具分页游标重复或无效，已停止查询。")
            seen_cursors.add(cursor)
            params = {"cursor": cursor}
        raise self._fail("MCP 工具分页过多，已停止查询。")

    def _validate(self, value: Any, schema: Any, path: str = "arguments") -> None:
        if not isinstance(schema, dict):
            return
        expected = schema.get("type")
        types = expected if isinstance(expected, list) else [expected]
        matches = {
            "object": isinstance(value, dict),
            "array": isinstance(value, list),
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "null": value is None,
        }
        known_types = [kind for kind in types if isinstance(kind, str) and kind in matches]
        if known_types and not any(matches[kind] for kind in known_types):
            raise self._fail(f"参数 {path} 类型不匹配，预期 {' / '.join(known_types)}。")
        if isinstance(value, dict):
            required = schema.get("required", [])
            if isinstance(required, list):
                for key in required:
                    if isinstance(key, str) and key not in value:
                        raise self._fail(f"缺少必需参数：{path}.{key}。")
            properties = schema.get("properties", {})
            if isinstance(properties, dict):
                for key, item in value.items():
                    self._validate(item, properties.get(key), f"{path}.{key}")
        elif isinstance(value, list) and isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                self._validate(item, schema["items"], f"{path}[{index}]")

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call only a locally allowed read tool; return its unchanged result."""
        if not isinstance(name, str) or name not in READ_ONLY_TOOLS:
            raise self._fail("此客户端仅允许预设的只读查询工具，已拒绝该调用。")
        if not isinstance(arguments, dict):
            raise self._fail("工具 arguments 必须是 JSON 对象。")
        tools = self._tools if self._tools is not None else self.list_tools()
        tool = next((item for item in tools if item["name"] == name), None)
        if tool is None:
            raise self._fail("当前 MCP 服务未提供所请求的只读工具。")
        self._validate(arguments, tool["inputSchema"])
        result, _ = self._rpc("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError") is True:
            details = "工具执行失败"
            content = result.get("content")
            if isinstance(content, list):
                texts = [item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"]
                details = " ".join(text for text in texts if isinstance(text, str)) or details
            raise self._fail(f"MCP 工具执行失败：{details}")
        return result
