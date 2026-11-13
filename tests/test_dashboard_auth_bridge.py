"""The wrapper owns native dashboard credentials, never the public browser."""
import asyncio
import importlib.util
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from starlette.requests import Request

ROOT = Path(__file__).resolve().parents[1]
with TemporaryDirectory() as home, patch.dict(os.environ, {
    "HERMES_HOME": home, "ADMIN_USERNAME": "bridge-test", "ADMIN_PASSWORD": "test-only",
}):
    spec = importlib.util.spec_from_file_location("bridge_server", ROOT / "server.py")
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)


class DashboardBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_dashboard_provisions_private_stable_token_only_to_its_child(self):
        dashboard = server.Dashboard()
        env = {"HERMES_HOME": "/isolated/profile", "HERMES_DASHBOARD_SESSION_TOKEN": "file:/operator-token"}
        original = dict(os.environ)
        spawn = AsyncMock(return_value=MagicMock(returncode=0))
        with patch.object(server, "build_hermes_env", return_value=env), \
             patch.object(server.asyncio, "create_subprocess_exec", spawn), \
             patch.object(dashboard, "_drain", AsyncMock()):
            await dashboard.start()
            first = spawn.call_args.kwargs["env"]["HERMES_DASHBOARD_SESSION_TOKEN"]
            await dashboard.start()
            await asyncio.sleep(0)
        self.assertNotEqual(first, env["HERMES_DASHBOARD_SESSION_TOKEN"])
        self.assertGreaterEqual(len(first), 43)
        self.assertEqual(first, spawn.call_args.kwargs["env"]["HERMES_DASHBOARD_SESSION_TOKEN"])
        self.assertEqual(env["HERMES_DASHBOARD_SESSION_TOKEN"], "file:/operator-token")
        self.assertEqual(dict(os.environ), original)
        self.assertNotIn(first, repr(dashboard.logs))
        self.assertIn("127.0.0.1", spawn.call_args.args)

    async def test_authenticated_http_replaces_untrusted_credentials(self):
        seen = []
        def native(request):
            seen.append(request)
            return httpx.Response(200, json={"ok": True})
        with patch.object(server, "get_http_client", return_value=httpx.AsyncClient(transport=httpx.MockTransport(native))) as get_client:
            async with get_client.return_value as upstream, httpx.AsyncClient(
                transport=httpx.ASGITransport(app=server.app), base_url="https://wrapper.test"
            ) as browser:
                response = await browser.get("/api/auth/me", headers={
                    "Cookie": f"{server.COOKIE_NAME}={server._make_auth_token()}; hermes_access_token=stale",
                    "Authorization": "Bearer attacker", "X-Hermes-Session-Token": "attacker",
                })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(str(seen[0].url), server.HERMES_DASHBOARD_URL + "/api/auth/me")
        self.assertEqual(seen[0].headers["authorization"], "Bearer " + server.dash._session_token)
        self.assertEqual(seen[0].headers["x-hermes-session-token"], server.dash._session_token)
        self.assertNotIn(server.dash._session_token, response.text)

    async def test_http_write_rejects_foreign_browser_origin(self):
        seen=[]
        def native(request):
            seen.append(request)
            return httpx.Response(200, json={"ok": True})
        async with httpx.AsyncClient(transport=httpx.MockTransport(native)) as upstream:
            with patch.object(server, "get_http_client", return_value=upstream):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="https://wrapper.test") as browser:
                    browser.cookies.set(server.COOKIE_NAME, server._make_auth_token())
                    denied = await browser.post("/api/model/set", json={}, headers={"Origin":"https://attacker.test"})
                    self.assertEqual(denied.status_code, 403)
                    self.assertEqual(seen, [])
                    allowed = await browser.post("/api/model/set", json={}, headers={"Origin":"https://wrapper.test"})
                    self.assertEqual(allowed.status_code, 200)
                    self.assertEqual(len(seen), 1)

    async def test_internal_model_pin_uses_private_credential_without_html_scraping(self):
        requests = []
        def native(request):
            requests.append(request)
            return httpx.Response(200, json={"ok": True})
        async with httpx.AsyncClient(transport=httpx.MockTransport(native)) as upstream:
            with patch.object(server, "get_http_client", return_value=upstream):
                self.assertIsNone(await server.set_active_model_via_hermes("openai", "test-model"))
        self.assertEqual([r.url.path for r in requests], ["/api/model/set"])
        self.assertEqual(requests[0].headers["x-hermes-session-token"], server.dash._session_token)
        self.assertEqual(await server._get_hermes_session_token(), server.dash._session_token)

    async def test_websocket_uses_server_credential_after_cookie_auth(self):
        from urllib.parse import parse_qs, urlsplit
        from starlette.websockets import WebSocket
        socket = WebSocket({
            "type": "websocket", "scheme": "wss", "path": "/api/ws", "query_string": b"token=attacker&internal=attacker&session_id=abc",
            "headers": [(b"host", b"wrapper.test"), (b"origin", b"https://wrapper.test"), (b"cookie", f"{server.COOKIE_NAME}={server._make_auth_token()}".encode())],
            "server": ("wrapper.test", 443), "client": ("127.0.0.1", 1234),
        }, AsyncMock(return_value={"type": "websocket.connect"}), AsyncMock())
        upstream = MagicMock(close=AsyncMock())
        connect = AsyncMock(return_value=upstream)
        with patch.object(server.websockets, "connect", connect), \
             patch.object(server, "_ws_pump_client_to_upstream", AsyncMock()), \
             patch.object(server, "_ws_pump_upstream_to_client", AsyncMock()):
            await server.ws_proxy(socket)
        url = urlsplit(connect.call_args.args[0])
        self.assertEqual(url.hostname, "127.0.0.1")
        query = parse_qs(url.query)
        self.assertEqual(query["token"], [server.dash._session_token])
        self.assertNotIn("internal", query)
        self.assertEqual(query["session_id"], ["abc"])
        self.assertIs(connect.call_args.kwargs.get("proxy", "not-disabled"), None)

    async def test_native_logout_clears_wrapper_login(self):
        def native(request):
            return httpx.Response(302, headers={"location": "/login"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(native)) as upstream:
            with patch.object(server, "get_http_client", return_value=upstream):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),
                                             base_url="https://wrapper.test") as browser:
                    browser.cookies.set(server.COOKIE_NAME, server._make_auth_token(), domain="wrapper.test", path="/")
                    response = await browser.post("/auth/logout")
                    self.assertEqual(response.status_code, 302)
                    denied = await browser.get("/api/config")
                    self.assertEqual(denied.status_code, 401)

    async def test_websocket_error_logs_never_publish_private_token(self):
        from starlette.websockets import WebSocket
        socket = WebSocket({
            "type": "websocket", "scheme": "wss", "path": "/api/ws", "query_string": b"",
            "headers": [(b"host", b"wrapper.test"), (b"origin", b"https://wrapper.test"), (b"cookie", f"{server.COOKIE_NAME}={server._make_auth_token()}".encode())],
            "server": ("wrapper.test", 443), "client": ("127.0.0.1", 1234),
        }, AsyncMock(return_value={"type": "websocket.connect"}), AsyncMock())
        error = OSError("private upstream failed: " + server.dash._session_token)
        with patch.object(server.websockets, "connect", AsyncMock(side_effect=error)), patch("builtins.print") as printer:
            await server.ws_proxy(socket)
        self.assertNotIn(server.dash._session_token, str(printer.call_args_list))

    async def test_websocket_rejects_foreign_origin_before_using_private_token(self):
        from starlette.websockets import WebSocket
        sent = AsyncMock()
        socket = WebSocket({
            "type": "websocket", "scheme": "wss", "path": "/api/ws", "query_string": b"",
            "headers": [(b"host", b"wrapper.test"), (b"origin", b"https://attacker.test"), (b"cookie", f"{server.COOKIE_NAME}={server._make_auth_token()}".encode())],
            "server": ("wrapper.test", 443), "client": ("127.0.0.1", 1234),
        }, AsyncMock(return_value={"type": "websocket.connect"}), sent)
        connect = AsyncMock(side_effect=OSError("must not dial"))
        with patch.object(server.websockets, "connect", connect):
            await server.ws_proxy(socket)
        connect.assert_not_called()
        self.assertEqual(sent.call_args.args[0]["code"], 4403)

    async def test_native_response_never_publishes_bridge_token_even_without_public_url(self):
        token = server.dash._session_token
        def native(request):
            return httpx.Response(200, text=f'<html><script>window.__HERMES_SESSION_TOKEN__="{token}";</script></body></html>',
                                  headers={"content-type": "text/html", "x-debug": token})
        async with httpx.AsyncClient(transport=httpx.MockTransport(native)) as upstream:
            with patch.object(server, "get_http_client", return_value=upstream):
                request = Request({"type": "http", "method": "GET", "path": "/", "query_string": b"", "headers": []}, AsyncMock(return_value={"type": "http.request", "body": b""}))
                response = await server._proxy_to_dashboard(request)
        self.assertNotIn(token.encode(), response.body)
        self.assertNotIn(token, str(response.headers))

    async def test_dashboard_logs_redact_private_credential(self):
        dashboard = server.Dashboard()
        async def output():
            yield f'WebSocket /api/ws?token={dashboard._session_token}'.encode()
        dashboard.proc = MagicMock(stdout=output(), returncode=0)
        with patch("builtins.print") as printer:
            await dashboard._drain()
        self.assertNotIn(dashboard._session_token, str(printer.call_args_list))
        self.assertNotIn(dashboard._session_token, str(dashboard.logs))

    async def test_dashboard_http_transport_ignores_environment_proxies(self):
        with patch.object(server, "_http_client", None):
            client = server.get_http_client()
            try:
                self.assertFalse(client._trust_env)
            finally:
                await client.aclose()


if __name__ == "__main__":
    unittest.main()
