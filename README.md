# Hermes Agent — Railway Template

Deploy [Hermes Agent](https://github.com/NousResearch/hermes-agent) on [Railway](https://railway.app) with a web-based admin dashboard for configuration, gateway management, and user pairing.

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/deploy/hermes-agent-ai?referralCode=QXdhdr&utm_medium=integration&utm_source=template&utm_campaign=generic)

> Hermes Agent is an autonomous AI agent by [Nous Research](https://nousresearch.com/) that lives on your server, connects to your messaging channels (Telegram, Discord, Slack, etc.), and gets more capable the longer it runs.

<!-- TODO: Add dashboard screenshot -->
<!-- ![Dashboard](docs/dashboard.png) -->

## Features

- **Admin Dashboard** — dark-themed setup wizard at `/setup` to configure providers, channels, tools, and manage the gateway
- **Full Hermes Dashboard** — the native Hermes web UI (Chat, Keys, Skills, Kanban, Analytics, Console) is proxied at `/`, behind the same login
- **One-Page Setup** — provider dropdown, checkbox-based channel/tool toggles — no config files to edit
- **Gateway Management** — start, stop, restart the Hermes gateway from the browser, with automatic restart if it crashes
- **Live Status** — stat cards for gateway state, uptime, model, and pending pairing requests
- **Live Logs** — streaming gateway log viewer
- **User Pairing** — approve or deny users who message your bot, revoke access anytime
- **Password-Protected** — one cookie-based login guards both the setup wizard and the Hermes dashboard
- **Reset Config** — one-click reset to start fresh
- **Backup & Restore** — download a full snapshot (config, credentials, chat history, memories, skills) as a zip, and restore it — including into a fresh project — to clone a deployment. Not encrypted; a safety snapshot is taken automatically before every restore.

## Getting Started

The easiest way to get started:

### 1. Get an LLM Provider Key (free)

1. Register for free at [OpenRouter](https://openrouter.ai/)
2. Create an API key from your [OpenRouter dashboard](https://openrouter.ai/keys)
3. Pick a free model from the [model list sorted by price](https://openrouter.ai/models?order=pricing-low-to-high) (e.g. `google/gemma-3-1b-it:free`, `meta-llama/llama-3.1-8b-instruct:free`)

### 2. Set Up a Telegram Bot (fastest channel)

Hermes Agent interacts entirely through messaging channels — there is no chat UI like ChatGPT. Telegram is the quickest to set up:

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot`, follow the prompts, and copy the **Bot Token**
3. Send a message to your new bot — it will appear as a pairing request in the admin dashboard
4. To find your Telegram user ID, message [@userinfobot](https://t.me/userinfobot)

### 3. Deploy to Railway

1. Click the **Deploy on Railway** button above
2. Set the `ADMIN_PASSWORD` environment variable (or a random one will be generated and printed to deploy logs)
3. Attach a **volume** mounted at `/data` (persists config across redeploys)
4. Open your app URL — log in with username `admin` and your password

### 4. Configure in the Admin Dashboard

1. **LLM Provider** — select OpenRouter from the dropdown, paste your API key, enter the model name
2. **Messaging Channel** — check Telegram, paste the Bot Token from BotFather
3. Click **Save & Start** — the gateway will start and your bot goes live

### 5. Start Chatting

Message your Telegram bot. If you're a new user, a pairing request will appear in the admin dashboard under **Users** — click **Approve**, and you're in.

<!-- TODO: Add Telegram chat screenshot -->
<!-- ![Telegram Example](docs/telegram-example.png) -->

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `8080` | Web server port (set automatically by Railway) |
| `ADMIN_USERNAME` | `admin` | Login username |
| `ADMIN_PASSWORD` | *(auto-generated)* | Login password — if unset, a random password is printed to the deploy logs. Changing it redeploys the service, which signs everyone out. |
| `HERMES_REF` | `v0.23.1` (Docker build arg) | Build selector, not a runtime version badge. Must match the admitted release commit; remove stale Railway overrides before upgrading — see [Updating Hermes](#updating-hermes). |

All other configuration (LLM provider, model, channels, tools) is managed through the admin dashboard.

## Supported Providers

Selectable from the setup wizard's dropdown:

OpenRouter, Anthropic (Claude), Google AI Studio, xAI (API key **or** SuperGrok OAuth), DeepSeek, Qwen Cloud (DashScope), GLM / Z.AI, Kimi, MiniMax (global **and** China), NVIDIA NIM, Fireworks AI, NovitaAI, Arcee AI, Step Plan, GMI Cloud, Hugging Face, GitHub Copilot, OpenCode Zen, OpenCode Go, Kilo Code, Ollama Cloud, AWS Bedrock, Azure Foundry, and any OpenAI-compatible **Custom Endpoint**.

Every other provider Hermes supports can still be configured from the Hermes Dashboard → **Keys** tab — the wizard covers the common ones, not the limit.

## Supported Channels

Telegram, Discord, Slack, WhatsApp, Email, Mattermost, Matrix

## Supported Tool Integrations

Parallel (search), Firecrawl (scraping), Tavily (search), FAL (image gen), Browserbase, GitHub, OpenAI Voice (Whisper/TTS), Honcho (memory)

## Architecture

One container runs a single public process that fronts two managed Hermes subprocesses:

```
Railway Container
└── server.py — Starlette + Uvicorn on 0.0.0.0:$PORT   (the only public surface)
    ├── /login, /logout    — cookie login (7-day, httponly)
    ├── /health            — health check (no auth)
    ├── /setup             — this template's setup wizard
    ├── /setup/api/*       — config, status, logs, gateway, pairing, backup, OAuth
    ├── /  and  /*         — reverse-proxied to the native Hermes dashboard
    │
    ├── hermes dashboard   — native Hermes web UI, bound to 127.0.0.1:9119
    └── hermes gateway     — the agent itself (Telegram, Discord, …), auto-restarted
```

The Hermes dashboard is **never exposed directly** — it binds loopback and is reachable only through the proxy, so one login covers both UIs. The gateway is supervised: if it crashes or is OOM-killed, `server.py` restarts it with backoff, giving up only if it fails repeatedly (Railway would not restart it on its own, because `server.py` is still alive and healthy).

For v0.23.1, a saved `dashboard.public_url` requires native authentication even
on loopback. The wrapper provisions a random, process-private deployment token
only to its dashboard child, and authenticates upstream HTTP/WebSocket requests
only after validating the admin cookie. This token is not stored in your profile,
sent to the gateway, or published to the browser. Public URLs and native auth
remain enabled; neither needs to be removed to make an existing volume boot.
The native dashboard's logout button also clears the wrapper login. Release CI
covers this public-URL upgrade case with protected APIs, a verified native
identity, built UI assets, and an authenticated WebSocket—not just `/health`.

Config lives on the `/data` volume at `/data/.hermes/` (`.env`, `config.yaml`, `auth.json`, sessions, pairing state) and survives redeploys. Gateway output is captured into a ring buffer and streamed to the Logs panel.

## Running Locally

```bash
docker build -t hermes-agent .
docker run --rm -it -p 8080:8080 -e PORT=8080 -e ADMIN_PASSWORD=changeme -v hermes-data:/data hermes-agent
```

Open `http://localhost:8080` and log in with `admin` / `changeme`.

## Updating Hermes

### v0.23.1 custom-image upgrade

This custom template pins **Hermes v0.23.1** at upstream commit
`3b421578588243f920886e6fe79a6faa87ea026e`. Both `HERMES_REF` and
`HERMES_COMMIT` must match the admitted release; an arbitrary ref override is not
an upgrade procedure. A stale Railway **`HERMES_REF` service/build variable** can
override the Dockerfile default: remove it or update it to `v0.23.1` before
building. The build helper also validates the admitted commit.

The runtime moves from the older Python 3.11 / ad-hoc install path to **Python
3.14** with the upstream package manager (**PM**) owning pinned Python, uv,
**Node >=26**, and pnpm from the release's `pm/lock.json`. PM's sealed environment
and frontend build scripts prepare the agent dependencies, native web assets,
and TUI bundle. This is not just a version-label change. The wrapper's
`requirements.txt` dependencies are installed into that same environment under
constraints captured from the agent dependencies; conflicts fail the build
rather than silently upgrading the agent. The loaded SQLite library remains
**>=3.51.3**, including functional FTS5 trigram support.

This remains the **custom Railway UI and supervisor**, not a migration to the
official image's s6 supervisor. Public port `$PORT` (default `8080`), private
loopback dashboard `9119`, and persistent `/data` volume are retained.
`HERMES_TUI_DIR=/opt/hermes-agent/ui-tui` is retained for the prebuilt TUI.
The gateway uses `hermes gateway run --external-supervisor` so this template,
not a second upstream supervisor, owns restarts. The runtime version badge reads
the built `install-stamp.json` through Hermes version metadata, **not the
`HERMES_REF` environment variable**. The stamp and image provenance describe the
actual installed version/commit with `updateMechanism: external`.

#### Safe rollout

1. **Before any actual deployment, back up all of `/data/.hermes`**, including
   every profile, config, `.env`, authentication, sessions, databases, and state.
   Store the backup securely outside the container; it contains credentials.
   Confirm you can restore it. Preserve the existing `/data` volume and keep the
   previous image/release available for rollback.
2. Review [upstream release notes](https://github.com/NousResearch/hermes-agent/releases/tag/v0.23.1),
   [current Hermes documentation](https://hermes-agent.nousresearch.com/docs/),
   and the release's `pyproject.toml` / PM lock. Dependency/build ownership is in
   `Dockerfile` and `scripts/build_hermes.py`; wrapper dependencies remain in
   `requirements.txt`. Wrapper startup/gateway compatibility is in `start.sh`
   and `server.py`. This change also updates release CI, runtime probes, and
   these upgrade notes; the SQLite-only CI is retained separately.
3. **Build and test a staging image first**, with no production volume or
   provider credentials. CI runs wrapper unit tests, the full Docker build,
   offline runtime checks, then ephemeral wrapper and native-dashboard smoke
   checks. Run the same image checks locally on a Docker-equipped host:

   ```bash
   docker build -t hermes-template:v0.23.1-test .
   bash tests/test_runtime_image.sh hermes-template:v0.23.1-test
   ```

   These checks use disposable containers, no network, a read-only probe mount,
   and clearly fake admin credentials. They check CLI version/help but do not
   start a gateway, query a provider, or test real messaging/order integrations.
   `/health` alone is not dashboard readiness: the native dashboard's HTML and
   built JavaScript must also respond on container-local `9119`.
4. Only after reviewing successful build/smoke results, merge the update into
   your linked deployment branch. Railway rebuilds and redeploys automatically
   when GitHub auto-deploy is enabled; no separate Railway settings change or
   manual restart is normally required. Otherwise use your existing deployment
   workflow. Keep the **existing volume preserved**. Verify the running
   version/commit from runtime metadata, login, and native dashboard
   readiness. Validate your own provider/channel configuration separately when
   ready. A git push or changed build argument alone does **not** prove a running
   deployment has upgraded.

Do not reset configuration as an upgrade step. These instructions make no
promise of automatic profile/config/database migration or rollback compatibility.
If state compatibility is uncertain, test a securely isolated backup copy before
reusing live state. Image-managed installs use external updates: rebuild and
redeploy the admitted image instead of trying to self-update the running
container. Future upstream versions need an explicit review of the release
commit, PM/toolchain pins, extras, build scripts, wrapper compatibility, and
verification expectations—not merely a changed `HERMES_REF`.

## Credits

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) by [Nous Research](https://nousresearch.com/)
- UI inspired by [OpenClaw](https://github.com/praveen-ks-2001/openclaw-railway) admin template
