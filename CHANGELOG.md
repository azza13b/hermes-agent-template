# Changelog

Release notes for this Railway template. The user-facing copy of this lives in
the admin UI under **What's New** (`templates/index.html`) — the two are kept in
sync; see `CLAUDE.md` → *Release workflow*.

**Branch naming:** `release/<hermes-version>/<n>`. `/1` is where that Hermes
version first landed; `/2`, `/3` … are template-only fixes on top of it. The
Hermes version never changes within a series. `main` always holds the newest
release.

---

## v0.23.1 custom-image upgrade (staging verification required)

- Pin upstream Hermes **v0.23.1**, commit
  `3b421578588243f920886e6fe79a6faa87ea026e`, with canonical install-stamp
  metadata and image provenance; updates are image-managed (`external`).
- Migrate the build/runtime to **Python 3.14** and upstream PM-owned pinned
  Python/uv/Node >=26/pnpm, sealed dependencies, and prebuilt web/TUI artifacts.
  Wrapper requirements are constrained against agent dependencies. Keep loaded
  SQLite >=3.51.3 with FTS5 trigram support.
- Retain this template's custom UI, public `$PORT`, private dashboard `9119`,
  `/data` volume, and `HERMES_TUI_DIR`; no migration to the official s6 image.
  Gateway subprocesses use the external-supervisor CLI contract. Version badges
  read runtime install metadata, not the `HERMES_REF` environment label.
- Add full-image CI for pull requests, manual dispatch, and pushes to `main` or
  `upgrade/hermes-v0.23.1`, alongside the unchanged SQLite-only workflow. Run
  isolated wrapper unit tests with fake admin credentials, then full build,
  offline runtime/CLI checks, and ephemeral wrapper/login/native-dashboard
  readiness checks. No real credentials, provider calls, or gateway starts are
  required by these smoke checks.
- Document staging-first rollout, stale Railway `HERMES_REF` override removal,
  and a secure backup of **all `/data/.hermes` profiles/config/auth/state** before
  actual deployment. Preserve the volume; do not reset config. No automatic
  migration or successful production upgrade is implied by a git push.

Full Docker build and container smoke results must be obtained on a
Docker-equipped runner before rollout; these release notes are not proof of a
successful build or live deployment. The historical admin UI release copy below
has not been rewritten as part of these documentation/CI changes.

---

## release/v2026.7.20/2 — July 30, 2026
**Hermes v2026.7.20 · minor**

### Bug fixes
- **Backup restore on cloud browsers** — "Choose file" did nothing on streamed
  browsers, which never surface the file dialog the old hidden-input picker
  relied on. The input is now a real, focusable control. ([#76](https://github.com/praveen-ks-2001/hermes-agent-template/issues/76))

### Improvements
- **Backup restore** — a .zip can be dragged onto the Restore box, and the
  outcome (success / warning / failure reason) now shows in the box rather than
  only as a brief toast.
- **MiniMax (China)** added to the provider dropdown alongside the global one.
  They are separate MiniMax platforms with separate keys, so both can be
  configured at once. Model hints (`MiniMax-M3`, `MiniMax-M2.7`) added for both.

---

## release/v2026.7.20/1 — July 27, 2026
**Hermes v2026.7.20 · major (Hermes upgrade, from v2026.7.1)**

### Hermes update
- Hermes Agent **v2026.7.1 → v2026.7.20** — adds the Hermes Console, session
  export, and three providers (Fireworks AI, DeepInfra, Upstage Solar).

### Changes to support upstream updates
- **Hermes Console** — new WebSocket route added to the proxy's fail-closed
  allowlist, which otherwise 403s it at our edge.
- **Restart throttling** — Hermes added its own respawn brake that blocks before
  the gateway boots. Disabled via `HERMES_GATEWAY_MAX_STARTS=0` so only this
  template's supervisor throttles; repeated saves no longer take the bot offline.
- **Backups** — `hermes backup` can now drop `state.db` and still exit 0. The
  archive is verified directly: a restore aborts unless its safety snapshot is
  complete, and downloads warn instead of handing over a partial file.
- **Paired users** — Hermes now re-copies the inactive pairing dir on every
  start, resurrecting revoked users. The two dirs are consolidated after a
  restore and at boot, and the store is resolved per request rather than cached.
- **Long replies** — Hermes disabled its loopback WebSocket keepalive; the proxy
  now matches it, so Chat no longer drops mid-reply.
- **MCP sign-in** — `HERMES_DASHBOARD_PUBLIC_URL` is derived from
  `RAILWAY_PUBLIC_DOMAIN`, since Hermes builds its OAuth return address from a
  Host header this proxy must rewrite.
- **Memory providers** — a new dashboard button installs into the running
  container with no immutability check; a warning is injected before it runs.
- **Conversation auto-reset** — upstream flipped the default, which would have
  split behaviour between new and existing volumes. Now pinned explicitly.

### Bug fixes
- **Users tab** read the wrong pairing location after a restore — requests could
  be invisible and approvals ignored until the next restart.
- **Gateway shutdown** waits longer, so multiple chat platforms disconnect
  cleanly instead of being cut off.
- **Save & Start on mobile** — the bottom bar sat below the visible viewport
  with no way to scroll to it (`100vh` vs the visible area).

### Improvements
- Sidebar shows the pinned Hermes version, linking to What's New.

---

## v2026.7.1-update — July 13, 2026
**Hermes v2026.7.1 · major**

> Predates the `release/<version>/<n>` convention, so it keeps its original
> branch name.

- **Backup & Restore** added under **Data** — download a full snapshot (config,
  provider keys, channel tokens, approved users, chat history, memories, skills,
  cron jobs) as a zip and restore it, including into a fresh project. A safety
  snapshot is taken automatically before every restore.
