# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A local [n8n](https://n8n.io) instance run with Docker Compose, where **workflows live in the repo
as JSON** under `workflows/` and are imported into n8n on every startup. There is no `package.json`,
no build, and no test suite — the deliverable is the compose file plus the workflow JSONs.

Prose (README, comments, node names, UI strings) is written in **Spanish**, per the parent workspace
convention. Keep it that way.

Current workflows: `hola-mundo` (smoke test), `test-telegram` (validates the Telegram credential and
chat ID without spending Anthropic credits), `noticias-ia` (the real one — daily 09:00 Europe/Madrid
AI-news digest: 9 RSS feeds → dedupe/rank → Claude summarizes → Telegram), and `noticias-ia-now`
(same pipeline, manual trigger only, window = the last 24 h counted from the moment you run it, so
it reports *now* instead of yesterday).

## Commands

```bash
docker compose up -d                              # start (re-imports workflows every time)
docker compose up -d --force-recreate n8n-import  # re-import only, without restarting n8n
docker compose logs n8n-import                    # check what the import did
docker compose exec n8n n8n list:workflow         # list workflows + active state
docker compose down                               # stop (data survives in the volume)
docker compose down -v                            # stop and DESTROY workflows, credentials, users
```

Run a workflow headlessly — the main way to verify changes:

```bash
docker compose exec -e N8N_RUNNERS_BROKER_PORT=5699 n8n n8n execute --id noticias-ia-diarias
```

`N8N_RUNNERS_BROKER_PORT` must differ from 5679, which the running instance already holds. Without
it the command fails with "n8n Task Broker's port 5679 is already in use".

Inspect the database (`better-sqlite3` is not resolvable inside the container; Node 24's built-in
module is):

```bash
docker compose exec n8n node -e "
const {DatabaseSync}=require('node:sqlite');
const db=new DatabaseSync('/home/node/.n8n/database.sqlite');
console.log(db.prepare('SELECT id,name,active FROM workflow_entity').all());
console.log(db.prepare('SELECT id,name,type FROM credentials_entity').all());
"
```

## Architecture

Two services sharing one volume (`n8n_data`, holding the SQLite DB — SQLite is an embedded library
inside the n8n image, not a separate service):

- **`n8n-import`** — one-shot init container. Imports `workflows/*.json`, then re-publishes the ones
  marked active. `n8n` waits on it via `depends_on: {condition: service_completed_successfully}`.
- **`n8n`** — the instance itself, on `${N8N_PORT:-5678}`.

**The JSON files are the source of truth.** Re-importing **overwrites** anything in the DB with the
same `id`. Editing a workflow in the UI and then running `docker compose up -d` silently discards
those edits. Since Code nodes hold JavaScript embedded in the JSON (painful to edit by hand because
of escaping), the workflow is: **edit in the UI → export over `workflows/<name>.json`**.

Credentials are *not* in the JSON — they live encrypted in the DB (keyed by `N8N_ENCRYPTION_KEY`),
so re-importing never forces credential reconfiguration.

## Gotchas that cost real debugging time

**`import:workflow` always deactivates everything it imports.** The `--activeState=fromJson` flag
that would fix this only works in queue/multi-main mode and hard-fails in a normal single instance.
Left uncompensated, *every* `docker compose up -d` silently disarms the cron. The `n8n-import`
command therefore runs `publish:workflow` over every JSON with `"active": true`. **`"active": true`
in the JSON is the on/off switch** — the UI toggle gets overwritten on the next import. Use
`publish:workflow`, not `update:workflow --active=true` (deprecated).

**Credentials must be referenced by id in the node's JSON.** A credential existing in the DB is not
enough; the UI auto-selects when there is exactly one of a type, but an *imported* workflow does not.
Symptoms: `Credentials not found` (HTTP Request node) or `Node does not have any credentials set`
(app nodes). The block looks like:

```json
"credentials": { "anthropicApi": { "id": "zNBMuk8BJyegIMaw", "name": "Anthropic account" } }
```

Those ids are **local to this volume** — wiping it or moving machines invalidates them, and a stale
id fails exactly like a missing one. Read the real ones out of the DB (query above) instead of
trusting the JSON. Current: `zNBMuk8BJyegIMaw` (`anthropicApi`), `56ozS4ND6jzpi6Bf` (`telegramApi`).

**`new URL()` does not exist in the Code node sandbox.** It throws, and a `try/catch { continue }`
around it will silently drop every item (this once turned 283 RSS items into 0 candidates with no
error surfaced). Parse hosts with a regex.

**The Code node runs in the isolated task runner, which rejects every external `require()`.**
`require('luxon')` fails with `Module 'luxon' is disallowed` unless the module is allowlisted — hence
`NODE_FUNCTION_ALLOW_EXTERNAL=luxon` in the compose file. Note that Luxon's `DateTime` is *also*
exposed as a plain global, with no `require` at all; the `noticias-ia*` workflows use that form and
are therefore immune to this. Prefer the global.

**`$env` in expressions requires `N8N_BLOCK_ENV_ACCESS_IN_NODE=false`** (already set in the compose
file). Otherwise: `ExpressionError: access to env vars denied`.

**Schedule triggers cannot be started from the CLI** (`Missing node to start execution`). Every
scheduled workflow keeps a parallel `manualTrigger` node wired to the same first step, which also
gives a UI test button.

**`n8n execute` prints trailing non-JSON text after the JSON blob.** Slice from the first `{` to the
index of `Error executing workflow` before `JSON.parse`, then read
`data.resultData.runData[nodeName][0]` for per-node item counts and `.error`.

**In Git Bash, prefix `docker run -v` with `MSYS_NO_PATHCONV=1`** or container paths get mangled
(`-v "$X":/s` became `S:/`). An empty `workflows;C` directory in the repo root was fallout from this.

**Google News RSS**: `when:1d` returns almost only same-day items — useless for a previous-day
digest. Use `after:`/`before:` with explicit dates, generated at runtime from Luxon.

**The `anthropicApi` credential only injects `x-api-key`.** When calling the API from an HTTP Request
node, add `anthropic-version: 2023-06-01` manually.

**`fallbacks` is not accepted on `claude-sonnet-5`** — `400 invalid_request_error: 'claude-sonnet-5' does not
support the 'fallbacks' parameter`. Both `noticias-ia*` workflows run on Sonnet 5 (cheaper than Opus and plenty
for summarizing headlines), so the `fallbacks` field and its `anthropic-beta: server-side-fallback-*` header are
gone. A safety refusal now arrives as `stop_reason: 'refusal'`, which `Formatear mensaje` already throws on.
Re-adding either one means going back to Opus.

**`docker compose up -d --force-recreate n8n-import` returns before the import finishes.** Chaining an
`n8n execute` onto the same command line runs the *previous* version of the workflow — which looks like a
successful run of an edit that never landed. Wait for the container to exit first:
`until [ "$(docker inspect -f '{{.State.Status}}' n8n-import)" = exited ]; do :; done`.

## Verification loop

Never assume a workflow edit works — nothing here has automated tests, and several of the bugs above
produce *zero output with no error*. After changing a workflow JSON:

1. `docker compose up -d --force-recreate n8n-import` (wait for it to exit)
2. `docker compose logs n8n-import` — confirm the import and activation lines
3. `docker compose exec -e N8N_RUNNERS_BROKER_PORT=<free> n8n n8n execute --id <id>`
4. Parse the printed JSON and check item counts per node — an unexpected `items= 0` is the signal

The `noticias-ia` Code nodes emit a `diagnostico` counter object
(`{total, sinLink, sinFecha, fechaInvalida, fueraDeRango, sinHost, ok}`) precisely so a silent
filtering collapse is visible instead of looking like "a slow news day".

## Notes

- Secrets go in `.env` (gitignored); `.env.example` is the template. `TELEGRAM_CHAT_ID` is a **numeric
  chat id**, not the bot token — the token belongs in the n8n Telegram credential.
- Changing `N8N_ENCRYPTION_KEY` makes existing stored credentials undecryptable.
- `N8N_SECURE_COOKIE=false` is for local HTTP only; remove it behind HTTPS.
- Pinned to nothing — the image is `:latest` (n8n 2.33.7 as of 2026-08-10). In n8n 2.x workflows have
  draft and published versions; imports land as drafts until published.
