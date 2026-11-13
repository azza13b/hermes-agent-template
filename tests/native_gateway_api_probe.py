"""Exercise the real gateway's embedded API auth bind, without starting a bot."""
from __future__ import annotations

import asyncio
import importlib
import os
from tempfile import TemporaryDirectory
from types import SimpleNamespace


def main():
    import httpx

    with TemporaryDirectory() as home:
        # This is a disposable native home; no account, bot, queue or cron data.
        os.environ["HERMES_HOME"] = home
        os.environ["HERMES_DASHBOARD_PUBLIC_URL"] = "https://ci-hermes.invalid"
        importlib.import_module("hermes_cli.plugins").discover_plugins()
        GatewayDashboardMount = importlib.import_module("gateway.run_api").GatewayDashboardMount
        web = importlib.import_module("hermes_cli.web_server")

        state = SimpleNamespace(bound_host="127.0.0.1", bound_port=0)
        mount = GatewayDashboardMount(SimpleNamespace(session_authority=None), state)
        # This exact call raised SystemExit in the deployed gateway traceback.
        mount._bind_state(web)
        assert web.app.state.auth_required is True
        assert web.app.state.withhold_session_token is True
        providers = importlib.import_module("hermes_cli.dashboard_auth").list_providers()
        assert providers, "embedded gateway must have a real registered auth provider"

        async def verify():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web.app),
                                         base_url="https://ci-hermes.invalid") as client:
                denied = await client.get("/api/config")
                assert denied.status_code in (401, 403)
                identity = await client.get("/api/auth/me", headers={
                    "Authorization": "Bearer " + os.environ["HERMES_DASHBOARD_SESSION_TOKEN"]})
                assert identity.status_code == 200
                identity = identity.json()
                assert identity.get("user_id") and identity.get("provider") == "session-token"
        try:
            asyncio.run(verify())
        finally:
            mount.web = web
            mount._unbind_state()
    print("real embedded gateway API: public-URL auth gate and private provider verified")


if __name__ == "__main__":
    main()
