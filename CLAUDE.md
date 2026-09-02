# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A local [n8n](https://n8n.io) instance run with Docker Compose, where **workflows live in the repo
as JSON** under `workflows/` and are imported into n8n on every startup. There is no `package.json`,
no build, and no test suite — the deliverable is the compose file plus the workflow JSONs.

**The provider is swappable and the code must stay neutral about it.** `LLM_BASE_URL` picks it:
Anthropic by default, anything else treated as OpenAI-compatible (OpenRouter, Groq, Cerebras,
Moonshot/Kimi all speak it). Only `comun.py` knows the difference — `_peticion` builds the request
and `_leer` reads the response, one branch each. Every section returns a **neutral spec**
(`{system, usuario, esquema}`) from its `construir_peticion` and calls `preguntar(seccion, ...)`;
none of them names a provider, a header or a response shape. Keep it that way: a provider detail
leaking into a section is how this stops being swappable.

**The strict JSON schema is load-bearing, in both shapes.** Anthropic gets `output_config.format`,
OpenAI-compatible gets `response_format.json_schema` with `strict: true`. Drop `strict` and the JSON
arrives approximate, `preguntar` throws on parse, and every section dies in turn. `test_proveedor.py`
pins both shapes.

**Per-section models exist for a reason** (`DIGEST_MODEL_<SECCION>` over `DIGEST_MODEL`): summarising
headlines is mechanical, but `resumen` and `laboratorio` have to *judge* — and to return nothing when
nothing qualifies, which is precisely the instruction weaker models ignore. Cheap model on the
mechanical sections, good model on the judgement ones. Also note `laboratorio` is the only section
that sends **private** data (the owner's project inventory), which matters when picking a free model
whose terms may log prompts.

**Read env vars with `or`, never `get(k, default)`.** GitHub Actions injects an *empty* value for an
undefined `vars.X`, so the `get` default never fires and you silently get `""`.

**Sources declare their own weight, and that weight travels with each entry.** It used to be derived
from the *link's* host, which sank any source served through an intermediary — an Anthropic item
arriving via Google News landed on `news.google.com` and scored the minimum, however primary the
source. `leer_feeds` now tags every entry with its feed's weight, name and theme, and `seleccionar`
takes `max(PESOS[host], feed_weight)`. Adding a feed without a weight is the silent failure this
guards against: it enters at the floor and the cut expels it forever, which reads as a poor feed
rather than a misconfiguration. `test_fuentes.py` is the structural guard.

**Primary sources outrank the press deliberately** (4 vs 3): the announcement, not the write-up of
the announcement. This also sharpens the existing dedupe — lab post plus five write-ups collapse
into one entry with `apariciones: 6`.

**`CUPOS` is a floor *and* a ceiling per off-topic theme.** Music and fabrication items carry none of
the AI keywords the ranking scores on, so without a floor they never enter and those feeds are
decorative; once given their own keywords they over-competed and one prolific blog took 5 of 50
slots, hence the ceiling. Slots are always taken from or given to the worst `ia` item, never another
capped theme.

**Things deliberately left out, with the reason** — do not "helpfully" add them back: arXiv (`cs.AI`
alone is 352 entries/day and would drown the cut); X and LinkedIn for the Spanish-speaking educators
(no free API, Nitter instances are gone — `nitter.net` returns 410 — and LinkedIn forbids scraping,
so any bridge breaks silently); and the funding/valuation/acquisition keywords that used to be in
`CLAVES` (they boosted exactly the news the owner's profile discards).

**`laboratorio` crosses the day against the owner's own backlog, and stays silent by default.**
`laboratorio/*.md` is a hand-written inventory, one file per project; its contents go to the model
verbatim — deliberately unparsed, so any format a human writes works and no parser can fail silently.
Three invariants:

- **The match is against declared BLOCKERS, not project descriptions.** A tool fits a problem, not a
  project. An inventory that only says what each thing does gives the model nowhere to land a match,
  and the section goes permanently quiet for the wrong reason. `laboratorio/README.md` explains this
  to whoever writes the entries; keep that explanation intact.
- **No match means no message at all** — not a message saying there was nothing. An alert that
  arrives daily stops being an alert. `formatear_bloques` returns `[]`, and `test_laboratorio.py`
  pins that case first.
- **Project names are validated against the inventory**, same as repo names in `repos.py`: anything
  the model invents is dropped with a log warning.

An empty or missing `laboratorio/` is not an error — it logs and returns `[]`.

**The Actions digest has four sections.** `scripts/noticias_ia.py` is the entry point and holds the
AI-news section plus the orchestration (hour guard, section loop, Telegram send); `scripts/repos.py`
holds the GitHub-trending section; `scripts/resumen.py` holds the personal filter; `scripts/laboratorio.py` crosses the day against
`laboratorio/*.md` and runs last;
`scripts/comun.py` holds what they all need (Anthropic call, Telegram send, chunking, escaping,
timezone). Sections are isolated — one raising does not stop the others, and the job exits non-zero
if any failed *after* sending what worked. Only the news section has an n8n equivalent; that drift is
deliberate, not an oversight.

**`resumen` answers a different question and must keep doing so.** Sections 1-2 report what happened;
section 3 reports what changes what the reader can *do*. Two invariants:

- **It reads the raw candidate pools, never the other sections' picks.** `generar_noticias` and
  `repos.generar` return `(bloques, candidatos)` precisely for this. The news prompt ranks funding
  and acquisitions as importance criteria — exactly what `resumen` excludes — so starting from its
  top 10 would inherit the wrong filter, and the item that matters may sit at rank 30 of 40. If you
  ever "optimise" this by passing the shortlist, you have silently broken the feature.
- **Zero points is a correct answer.** The prompt is explicit that the right bias is to discard and
  that padding with the least-bad item is a failure. A summary that finds five important things every
  day cannot mark the day that matters. `test_resumen.py` pins the empty case first for this reason.

The whole "for whom" lives at the top of `resumen.py`: `PERFIL` (interests and hard exclusions) and
`EJEMPLOS` (calibration cases the owner supplied — news that did matter, and the big-looking news
that did not). **There is no training loop anywhere in this repo**: these prompts are the entire
criterion. When the owner says "learn that X matters", the change is a line in one of those two
constants. Note the deliberate tension already encoded there: `PERFIL` excludes company politics
but carves out legal and regulatory disputes involving labs whose tools he depends on, because
those can change his access. Do not "clean up" that contradiction — it is the point.

`scripts/test_repos.py`, `scripts/test_resumen.py` and `scripts/test_laboratorio.py` are the
repo's tests: no dependencies, no network (it swaps
`repos._buscar` for a fixture), run it with `python3 scripts/test_repos.py`. It pins the filtering,
the stars/day ranking and the message formatting — including that the numbers in the message come
from the search response and never from the model. Run it after touching `repos.py`.

**GitHub publishes no trending API — so `repos.py` scrapes `github.com/trending`, deliberately.**
There is no RSS, no API, no feed: the page is the only source. Do not "fix" this by switching to the
search API and deriving velocity from `stars / age` — that computes a number GitHub already
publishes exactly (`1,234 stars today`), and worse. The README's "no HTML scraping" rule is about
**news sources**, and its stated reason is that outlets *do* publish RSS; it does not generalise to
sources that have no machine interface at all.

Scraping's cost is fragility, mitigated three ways, all of which must survive any edit here:
anchor on semantics (`/stargazers` href, `itemprop="programmingLanguage"`, the literal `stars today`)
and never on CSS classes like `Box-row`; raise loudly when zero repos parse, because trending always
has rows and a silent empty result would look like a quiet day; and keep the per-period diagnostic
(`{articulos, sinNombre, sinGanadas, ok}`) so a markup change is diagnosable from the job log alone.

Three traps already paid for in `parsear()`. `<p[^>]*>` also matches `<path>` inside the title's
`<svg>` (hence `<p\b`). Scraped HTML carries entities that RSS did not — strip tags first, then
`html.unescape`, never the other way round. And **numbers are not adjacent to the `>` that precedes
them**: GitHub puts `<svg class="octicon octicon-star">` inside the stargazers link, so a pattern
demanding digits right after the tag silently yielded `estrellas: 0` on every row while the fixture
(which lacked the icon) stayed green. `RE_STARGAZERS` now captures the whole `<a>` body and cleans it
afterwards. General lesson for this file: **capture the container, clean it after — never assume
where inside an element the text sits.**

Because a partial parse fails silently by design (one missing field is not fatal), `parsear()` counts
`sinTotal`, `sinLenguaje` and `sinDescripcion` alongside `sinNombre`/`sinGanadas`. A nonzero count
across all rows means that field's anchor is gone, and the `--dry-run` listing prints the language so
the same failure is visible there too. Check those counters before believing a green run.

**The daily digest exists twice, on purpose.** `workflows/noticias-ia.json` runs it in local n8n at
11:00; `.github/workflows/noticias-ia.yml` + `scripts/noticias_ia.py` run the same pipeline on
GitHub's runners at 09:00 Europe/Madrid. The n8n copy only fires if this machine happens to be awake
— n8n does not replay missed schedule triggers — so the Actions copy is the one that does not skip
days, and the n8n UI is the comfortable place to *edit* the pipeline. The two share no code:
**a change to the ranking, the prompt, the schema or the sources must be made in both**, or they
drift. The Python port is a faithful translation of the Code nodes, down to the scoring formula and
the dedupe key.

Prose (README, comments, node names, UI strings) is written in **Spanish**, per the parent workspace
convention. Keep it that way.

Current workflows: `hola-mundo` (smoke test), `test-telegram` (validates the Telegram credential and
chat ID without spending Anthropic credits), `noticias-ia` (the real one — daily 11:00 Europe/Madrid
AI-news digest: 9 RSS feeds → dedupe/rank → Claude summarizes → Telegram), and `noticias-ia-now`
(same pipeline, manual trigger only, window = the last 24 h counted from the moment you run it, so
it reports *now* instead of yesterday).

## Commands

The Actions version, which needs no Docker and no n8n:

```bash
pip install -r requirements.txt
python scripts/noticias_ia.py --dry-run --force                 # rank candidates, call nothing
python scripts/noticias_ia.py --dry-run --force --secciones repos
python scripts/noticias_ia.py --force --ventana ultimas24h      # real send, last 24 h
python scripts/test_proveedor.py                                # provider swap, both shapes
python scripts/test_ventana.py                                  # digest window, DST + retries
python scripts/test_telegram.py                                 # Telegram retries, no network
python scripts/test_fuentes.py                                  # source-list structure
python scripts/test_repos.py                                    # fixture test, no network
python scripts/test_resumen.py                                  # summary test, no network
python scripts/test_laboratorio.py                              # lab cross-reference test
```

`--force` skips the "is it 09:00 in DIGEST_TZ?" guard; without it the script exits 0 doing nothing.
`--dry-run` is the cheap way to verify a change to the filtering or the ranking: it prints the
`diagnostico` counters and the top candidates without spending Anthropic credits.

The n8n instance:

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

**The GitHub Actions cron is UTC-only and Madrid observes DST.** A fixed cron would drift an hour
for half the year, so the workflow fires at **07:00, 08:00, 09:00 and 10:00 UTC** and
`noticias_ia.py` checks the local hour in `DIGEST_TZ`, exiting 0 on the runs that do not match.
Changing the timezone or the hour means changing *both* the env vars and those UTC cron hours.

**GitHub does not deliver `schedule` anywhere near the requested time.** This isn't a "the cron
occasionally gets dropped" problem — it's that the delay is the normal case. On 2026-08-27 neither
of the two crons that existed then fired at all. Then, with four crons (07-10 UTC) and a 3h window
(09:00-12:59 local), *every single scheduled run for a week* (2026-08-29 through 09-02, runs #8-13)
missed the window: GitHub delivered exactly one `schedule` event per day, 3 to 12 hours late
(12:03, 12:30, 15:00, 12:45, 13:07, 19:22 UTC — none within 3h of the 07-10 UTC cron), and
`en_ventana` rejected all six. Having multiple cron entries doesn't help against this: GitHub
still only delivers one event, just a late one — so the fix is not more crons, it's a wide enough
window to not reject the one that arrives. `DIGEST_MARGEN_HORAS` defaults to 14 (09:00-23:59) for
exactly this reason; if you find yourself narrowing it, first check whether GitHub's actual
delivery lag has improved, not just assume the old 3h reasoning still holds. Never *before* the
requested hour: a 09:00 digest must not go out at 08:00.

The marker in the Actions cache (`noticias-ia-enviado-<date>`) is what prevents a double send if
more than one `schedule` event ever does land the same day — with a 14h window that's the only
thing standing between a good day and duplicate digests, so don't remove it when touching the
cron or the margin. `test_ventana.py` checks the *default* margin (not a hardcoded test value)
against the actual UTC hours GitHub delivered in that first production week, converted to Madrid
local time — so it fails if the margin ever gets narrowed below what GitHub actually does.

## Gotchas that cost real debugging time

**A step `if:` without a status function carries an implicit `success()`.** The "Anotar que ya
salió" step guards on `hashFiles('.enviado/**')`, which reads like it only depends on the file — but
the script exits 1 when *any* section fails, so without `always()` the step is skipped, the marker
is never cached, and the next fire in the window resends every section that had worked. That got
much worse once the window allowed four fires instead of one. `always() && …` is load-bearing.

**One flaky HTTP call used to kill a whole section.** `enviar_telegram` caught only `HTTPError`, so
a read timeout propagated: on 2026-08-27 the first Telegram message of `noticias` timed out and the
section died with three sections still to go. Both API callers now share one retry policy — retry
`URLError`/`TimeoutError`, 429 (honouring Telegram's `retry_after`) and 5xx with exponential
backoff; give up immediately on any other 4xx, which means a bad token, a bad chat id or invalid
HTML and never gets better by insisting. Retrying a timeout can duplicate a message that did land;
that is the accepted trade against losing the section. `test_telegram.py` covers both directions.

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
