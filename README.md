# BTX Revenue Cockpit

A standalone deployment of `monitor-engine` for the BTX Chief Revenue Officer.
It ships **two views** off one static, no-backend site:

1. **Intelligence brief** (`index.html`) — the scored, tiered weekly brief of
   contract, program, policy, and supply-chain developments relevant to BTX,
   with cross-API enrichment and agentic deep analysis (see *Signal Mesh* below).
2. **Account Map** (`map.html`) — an **interactive map of potential accounts**,
   each pin scored for **fit against BTX's capabilities** (precision machining,
   5-axis CNC, build-to-print, AS9100/ITAR) with a "how BTX can serve them"
   readout. Click a pin for award context, fit rationale, and source links.

The two views cross-link in the header, so the CRO opens one URL and moves
between "what's happening" and "who to call."

**Architecture in one sentence:** A GitHub Actions workflow pulls sources +
accounts, scores them (Claude for the brief, deterministic rules for map fit),
and commits `index.html` + `map.html` + JSON artifacts back to the repo.
No server, no database, no always-on infrastructure.

## The Account Map

```
account_map config        clients/btx/config.json → region, center, segments, sources
       ↓
sources (pluggable)       CSV (your target list)  +  any API via the generic connector
       ↓                  (USAspending / SAM.gov / CRM — add as config, no code)
geocode                   lat/lon from the row, else US state-centroid fallback
       ↓
score_fit                 match BTX profile (capabilities, programs, customers)
                          against each account → fit 0–100 + "how we can serve them"
       ↓
write_map_site            map_targets.json (data) + map.html (interactive Leaflet page)
```

Build it: `python -m monitor_engine.targets --config clients/btx/config.json --output clients/btx/artifacts`

**Add accounts** by editing `clients/btx/data/target_accounts.csv` (name, segment,
city, state, lat, lon, + any fact columns), or add an `api` source under
`account_map.sources` pointing at any JSON API (the `{query}` connector).

**Live / authenticated company APIs (CRM, ERP, capacity):** the map is static, so
it can't hold a secret API key in the browser. To pull live per-account data
(current capacity, open quotes), point `getLiveAccountData()` in `map.js` at a
small serverless **proxy** that holds the key server-side. That proxy lives in
the deployment layer, not the engine — the seam is already wired and documented.

> Runtime dependency: the map page loads **Leaflet** + OpenStreetMap tiles from a
> CDN (a map needs network for tiles regardless). No Python dependency is added.

---

## How it works

```
load_feedback()       optional clients/<name>/feedback.json → mute/boost/pin rules
       ↓
collect_all()         pull from all sources in parallel
       ↓
keyword_prefilter     cheap OR/AND text filter; drops obvious noise
       ↓
Scorer.analyze()      LLM batch-classifies each item per edition (concurrently);
                      cleans titles; computes tier (1 Essential / 2 / 3 Tracked);
                      extracts named entities for cross-referencing
       ↓                ↳ [agentic research] (optional) top-tier items get a
                          tool-using Claude loop that calls live APIs (query_api)
                          and fetches pages (fetch_url); findings feed deep analysis
       ↓
group_related_items() collapses same-event duplicates into one "also covered by" card
       ↓
enrich_items()        (optional) resolve each item's entities against OTHER
                      configured APIs and attach structured facts ("Live data")
       ↓
build_entity_graph()  link items sharing entities → "Connected stories" on each
                      card + a run-level entity index (the entity explorer)
       ↓
compute_diff()        what's new vs. the previous run
       ↓
build_site()          inlines CSS+JS into a single index.html;
                      embeds run_output.json (the frontend reads it client-side)
       ↓
update_archive()      rolls the 26-run history; pins high-importance items
```

The pipeline is invoked as `python -m monitor_engine --config PATH --output DIR`.

### Signal Mesh — cross-API integration (optional)

Beyond classifying headlines, the engine can turn a feed into a **connected
intelligence system**. Three opt-in, config-driven capabilities, all generic
(no client/industry knowledge in the engine):

- **Entity extraction** — classification also pulls structured entities
  (`{name, type}`) from every item. Always on; the seed for the rest.
- **Cross-API enrichment** (`enrichers`) — for each entity, the engine queries
  *another* API you configure and attaches the results as facts. A contract
  award discovered in one feed gets resolved against a spending API for the
  awardee's totals; a program name against a rulemaking API for the latest
  notice. Lookups run in parallel, are capped per run, and fail soft (one bad
  lookup never aborts the run).
- **Entity graph** — items that share an entity are linked ("Connected stories"),
  and the connective entities become a browsable **entity explorer** on the site.
- **Agentic deep analysis** (`deep_analysis.agentic`) — top-tier items get a
  bounded Claude tool-use loop with two tools: `query_api` (run any configured
  enricher with a free query) and `fetch_url` (pull a page's text). Claude
  researches, then the deep-analysis prompt grounds and cites those findings.

A connector is a parameterized HTTP/JSON call: the token `{query}` in its `url`
or `request_body` is replaced with the entity being looked up. The same
connector shape backs both the enrichment stage and the agentic `query_api`
tool, so adding any API is one config block — never engine code.

---

## Quickstart: stand up a new client in ~10 minutes

### 1 — Fork or copy this repo

Create a new GitHub repository.  Copy this repo into it, or fork it.
The engine lives in `monitor_engine/`; you never touch that directory.

### 2 — Create your client directory

```
clients/
  my-client/
    config.json          ← the only file you write
    artifacts/           ← written by the pipeline; committed by CI
```

Copy `clients/btx/config.json` as a starting template.

### 3 — Edit `config.json`

| Field | What to set |
|---|---|
| `branding.name` | Display name shown on the site |
| `branding.accent_color` | Hex color for tier-1 cards, e.g. `"#1B4F8A"` |
| `editions` | 1–4 audience segments; each gets its own relevance score and category filter |
| `sources` | RSS, JSON API, or HTML list sources (see below) |
| `keyword_prefilter.include` | At least one of these keywords must appear in title+summary; leave empty to pass everything |
| `keyword_prefilter.exclude` | Items matching any of these are dropped before analysis |
| `scoring_rubric.thresholds` | Default `tier_1_min: 80`, `tier_2_min: 50`, `tier_3_min: 20`; tune per client |
| `scoring_rubric.never_discard` | Keywords that force an item to at least Tier 3, regardless of score |
| `profile` | Optional client profile (capabilities, certifications, goals, risks, named entities) — consumed by the analysis prompt so each item's "why it matters / what to do" is specific to this client |
| `cadence.cron` | Informational cadence (the pipeline is manual-dispatch only by default) |
| `cost_caps.max_items_per_run` | Hard limit on items sent to the LLM per run (default 50) |
| `enrichers` | Optional cross-API enrichment (see *Signal Mesh* above). Each enricher names the entity types it applies to and a `connector` (a `{query}`-parameterized HTTP/JSON call with a `fact_map`). Lookups are capped by `max_entities_per_run` |
| `deep_analysis.agentic` | Set `true` to give items in `agentic_tiers` (default `[1]`) a tool-using research pass before the deep write-up. Tune `max_research_steps` and `allow_fetch` |

Sources can also take a per-source `days_back` to widen the lookback for
low-frequency feeds (e.g. a quarterly report) that the global window would miss.

**Never put secrets in config.json.** For authenticated sources, set
`auth_env_var: "MY_API_KEY"` and declare that secret in GitHub repo settings
(Settings → Secrets and variables → Actions).

**Two optional sibling files** in `clients/<name>/`:
- `intake.json` — questionnaire answers; run `python -m tooling.scaffold intake.json`
  to (re)generate a draft `config.json`. The single-page `docs/intake/survey.html`
  produces this file from a form (no hand-editing).
- `feedback.json` — client feedback the next run honors deterministically
  (`mute_terms`, `boost_terms`, `mute_sources`, `suppress_urls`, `pin_urls`).
  Generate it from the dashboard's per-item 📌/🚫/🔇 controls + "Download
  feedback.json", or hand-edit (see `clients/btx/feedback.example.json`).

### 4 — Add your client to the dispatch options

Open `.github/workflows/monitor.yml` and add your client to the `client` input
options (the run matrix is built from this choice — `all` runs every client):

```yaml
inputs:
  client:
    options:
      - all
      - my-client
```

### 5 — (Optional) enable a schedule

The pipeline is **manual-dispatch only** by default — nothing runs on its own.
To add weekly runs later, uncomment the `schedule:` block in the workflow.

### 6 — Set the `ANTHROPIC_API_KEY` secret

GitHub repo → Settings → Secrets and variables → Actions → New repository secret.
Name: `ANTHROPIC_API_KEY`, value: your key from console.anthropic.com.

Add any source-specific API keys the same way, then reference them in the
workflow's `env:` block:

```yaml
env:
  ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
  MY_API_KEY: ${{ secrets.MY_API_KEY }}
```

### 7 — Run it (one button)

Actions → **Monitor Pipeline** → Run workflow → pick a client (or `all`). One
dispatch runs the whole flow end to end: **test gate → collect → analyse →
commit artifacts → publish to Pages**. To re-publish without re-running the
pipeline, dispatch **Deploy Pages**.

### 8 — Publish via GitHub Pages

Repo → Settings → Pages → Source: **GitHub Actions**. The pipeline's deploy step
(and the standalone Deploy Pages workflow) publishes a landing page linking every
client dashboard. Deploys run only from the default branch.

---

## Local testing

### Test source connectivity (no API key needed, no LLM calls)

```bash
pip install -e ".[dev]"

python -m monitor_engine.collectors \
  --config clients/btx/config.json \
  --days-back 14 \
  --max-items 3
```

This prints a summary table — items found, a sample title, any errors — for
every source in the config.  Use this to confirm a new source works before
wiring it into CI.

### Full pipeline without LLM (confirm the pipeline wiring)

```bash
python -m monitor_engine \
  --config clients/btx/config.json \
  --output /tmp/ae-test \
  --skip-analysis
```

Writes `index.html` and `run_output.json` to `/tmp/ae-test` with empty
items (no analysis), which is enough to verify config parsing, file I/O,
archive update, and HTML generation.

### Full pipeline with LLM

```bash
export ANTHROPIC_API_KEY=sk-ant-...

python -m monitor_engine \
  --config clients/btx/config.json \
  --output /tmp/ae-full \
  --days-back 7 \
  --max-items 10
```

Open `/tmp/ae-full/index.html` in a browser.

---

## Source configuration reference

### RSS

```json
{
  "type": "rss",
  "id": "my-feed",
  "name": "Human-readable name",
  "url": "https://example.com/feed.rss"
}
```

### JSON API

```json
{
  "type": "json_api",
  "id": "my-api",
  "name": "Human-readable name",
  "url": "https://api.example.com/v1/items?limit=20",
  "item_path": "$.results",
  "field_map": {
    "title": "title",
    "url": "html_url",
    "published_at": "publication_date",
    "summary": "abstract"
  },
  "auth_header": "X-Api-Key",
  "auth_env_var": "MY_API_KEY"
}
```

`item_path` is a dot-notation path to the array in the response, e.g.
`$.results`, `$.data.items`, `$.opportunitiesData`.

`field_map` maps the engine's field names (`title`, `url`, `published_at`,
`summary`) to whatever keys your API uses.

### HTML list

```json
{
  "type": "html_list",
  "id": "my-page",
  "name": "Human-readable name",
  "url": "https://example.com/news",
  "item_selector": "li.news-item",
  "title_selector": "h3",
  "link_selector": "a",
  "date_selector": "time"
}
```

---

## Files to edit vs. never touch

| Directory / file | Who edits it |
|---|---|
| `clients/<name>/config.json` | **You** — the main deployer file (sources, editions, profile, thresholds) |
| `clients/<name>/feedback.json` | **You / client** — optional; from the dashboard's Download button or by hand |
| `clients/<name>/intake.json` | **You** — optional; generated by `docs/intake/survey.html`, feeds `tooling.scaffold` |
| `.github/workflows/monitor.yml` | **You** — add your client to the dispatch `client` options |
| `clients/<name>/artifacts/` | **CI** — do not edit by hand |
| `monitor_engine/` | **Never** — engine internals; update via `pip install` upgrades |
| `pyproject.toml` | Engine maintainer only |

The principle: the engine is a dependency.  You configure it; you do not
modify it.

---

## Caveats

**LLM costs money.** Each weekly run for 50 items costs roughly $0.02–$0.08
depending on item length and model.  `cost_caps.max_items_per_run` is your
main lever.  The pipeline prints a cost estimate at the end of each run.

**The LLM can be wrong.** Relevance scores, tier assignments, and extracted
dollar amounts are LLM outputs.  Items with dollar/population figures that
don't match the source text are flagged in `unverified_claims`, but the
check has ~20% tolerance and misses some errors.  Do not feed the brief
directly to an audience without editorial review.

**Source URLs break.** RSS feeds change paths.  APIs change schemas.  Run
`python -m monitor_engine.collectors --config ...` regularly to spot dead
sources before they silently drain from your brief.

**No real-time updates.** The brief is as fresh as the last CI run.
Breaking news between runs won't appear.  Set a tighter cron if that matters
to your audience.

**Static site = no auth.** `index.html` is publicly readable if your repo
is public or if you publish it via GitHub Pages without access controls.
For confidential briefs, keep the repository private or add a reverse-proxy
with auth in front of the Pages URL.

**Archive retention.** The default rolling window is 26 runs (~6 months at
weekly cadence).  Items at Tier 1 are pinned beyond that window.  `archive.json`
grows over time; at 26 × 60 items it stays under 2 MB.

---

## Adding a SAM.gov source (example of an authenticated source)

1. Get an API key at https://sam.gov/content/entity-information/api
2. Add a secret `SAM_GOV_API_KEY` to your GitHub repo.
3. Reference it in the workflow `env:` block.
4. Add to `sources` in your config:

```json
{
  "type": "json_api",
  "id": "sam-gov-opportunities",
  "name": "SAM.gov Contract Opportunities",
  "url": "https://api.sam.gov/opportunities/v2/search?limit=20&ptype=o",
  "item_path": "$.opportunitiesData",
  "field_map": {
    "title": "title",
    "url": "uiLink",
    "published_at": "postedDate",
    "summary": "description"
  },
  "auth_header": "X-Api-Key",
  "auth_env_var": "SAM_GOV_API_KEY"
}
```
