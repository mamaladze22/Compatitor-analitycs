# How We Built the Meta Ads Competitor Tracker
### An Educational Guide — Georgian Publishing Market Intelligence System

---

## What This System Does

Every time you run `/scan`, it automatically:
1. Pulls every ad your competitors are running on Facebook/Instagram
2. Writes all of it to a Google Sheet (with history preserved)
3. Generates an opinionated intelligence report telling you what matters

No manual browsing. No copy-pasting. No stale screenshots. The whole thing runs in under 3 minutes.

---

## The Problem We Were Solving

**The Meta Ad Library** (`facebook.com/ads/library`) is a public transparency tool that shows every ad any brand is running on Facebook and Instagram. Meta was legally required to build it.

For a publisher like Sulakauri, this means you can see exactly:
- What ads Biblusi, Palitra L, Diogene, and others are running right now
- How long each ad has been running (longer = more likely profitable)
- What copy, format, CTA, and landing page they're using
- Whether they launched something new today

The problem: doing this manually for 6 competitors, weekly, is tedious and easy to forget. So we automated it.

---

## The Architecture

```
/scan (Claude Code slash command)
    ↓
meta-ads-analyst subagent (AI agent)
    ↓
scripts/fetch_ads.py          →  data/2026-05-14T13-30-35Z.json
    ↓ (Apify cloud)
Meta Ad Library (facebook.com/ads/library)
    ↓
scripts/update_sheet.py       →  Google Sheets (raw_ads tab)
                              →  data/latest_diff.json
    ↓
Analyst reads diff, writes    →  reports/2026-05-14.md
    ↓
scripts/format_sheet.py       →  Professional formatting on the Sheet
```

Each piece has one job. Nothing is hardcoded together.

---

## Piece 1 — Apify (the data fetcher)

### Why we needed it

Meta's Ad Library is a **React app**. When you visit it in a browser, the page loads blank HTML and then JavaScript fetches the actual ads via internal API calls. A normal HTTP request to the URL returns empty content — the data isn't in the HTML.

To get the data you need to run a **real browser**, wait for JavaScript to execute, then read the rendered result. This is called headless browser scraping.

### Why we didn't build it ourselves

Building a reliable headless scraper requires:
- Running a browser process (Playwright or Puppeteer)
- Handling dynamic loading and infinite scroll
- Rotating IP addresses so Meta doesn't rate-limit you
- Fixing the scraper every few months when Meta changes the page structure
- Hosting this on a server 24/7

Apify already built and maintains all of this. We pay a small amount per scan (roughly $1–2) and get back clean structured JSON. Their team fixes it when Meta changes things — not us.

### The actor we use

`apify/facebook-ads-scraper` — Apify's own officially maintained actor, not a community one.

**Why official matters:** Third-party actors go unmaintained when the author loses interest. Apify's own actor gets fixed promptly when Meta changes the Ad Library because it's their product.

### How it works technically

We give Apify a list of URLs in this format:
```
https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=GE&view_all_page_id=176611737863
```

That URL is exactly what you'd see in your browser when you click "See all ads" on a Facebook page. The `view_all_page_id` parameter tells the Ad Library to show only that page's ads.

Apify runs a browser against that URL, scrapes all results, and returns JSON objects — one per ad.

### Why page IDs, not page names

Page names change. `Palitra.L.Publishing` could rename their page tomorrow. The numeric page ID (`204459169625213`) never changes — it's Meta's internal identifier assigned when the page was created.

We store page IDs in `config/advertisers.yaml` and build URLs from them. The one-time `--resolve-only` step fetches a few ads per advertiser to discover their ID, then we store it permanently.

### Your IP is not exposed

When Apify runs the scraper, requests come from **Apify's servers and proxy network**. Meta sees Apify's infrastructure, not your IP or your account. You're one layer removed. Combined with the fact that the Ad Library is a public transparency tool (Meta's own legal obligation), the risk profile for this use case is very low.

---

## Piece 2 — The Python Scripts

### `fetch_ads.py`

**Input:** `config/advertisers.yaml` (list of competitors + page IDs)  
**Output:** `data/2026-05-14T13-30-35Z.json` (all ads, normalized)

The script:
1. Reads the YAML config
2. Builds an Apify actor input (list of Ad Library URLs, one per competitor)
3. Calls the Apify API, waits for the run to finish
4. Normalizes each raw ad into our consistent schema (field names, formats)
5. Writes a timestamped JSON file

**Key design decision — normalization:** Apify's actor returns fields in camelCase (`adArchiveID`, `startDateFormatted`). Our system uses snake_case (`ad_id`, `start_date`). The `normalize_ad()` function converts between them so the rest of the codebase never touches raw Apify output.

**The `--resolve-only` flag:** Before you can scan properly, you need each competitor's numeric page ID. This flag runs the actor with `resultsLimit=5` (just enough to see one ad and extract the page ID from it), then prints the IDs. You run it once, copy the IDs into the YAML, and never run it again.

### `update_sheet.py`

**Input:** `data/latest scan.json` (from fetch_ads.py)  
**Output:** Updated Google Sheet + `data/latest_diff.json`

The sheet is **append-only history**. The script never edits old rows — it only:
- **Appends** rows for ads never seen before (`first_seen` = today)
- **Updates** `last_seen` for ads already in the sheet that are still running
- **Marks retired** any ad that was active last scan but not found in this scan

This gives you a permanent record of every ad every competitor ever ran, when it started, and when it stopped.

The **diff** (`latest_diff.json`) is a summary: per advertiser, how many new ads, how many retired, how many still running, what's the longest-running ad. This is what the analyst reads — not the raw 126-row JSON.

### `format_sheet.py`

**Input:** The live Google Sheet  
**Output:** Same sheet, but with professional formatting

Sends one batch of formatting requests to Google Sheets API:
- Deep navy header row with white text
- Frozen first row and first column (so headers always visible when scrolling)
- Alternating row colors (white / light grey-blue)
- Green highlight for `is_active = TRUE`, red for `FALSE`
- Auto-filter on all columns
- Specific column widths for each field
- Body text column set to word-wrap so long ad copy is readable

Safe to run repeatedly — it's idempotent (running it twice produces the same result).

---

## Piece 3 — Claude Code Integration

### The `/scan` slash command

When you type `/scan` in Claude Code, it invokes a custom slash command defined in `.claude/commands/scan.md`. That command delegates all work to the `meta-ads-analyst` subagent.

**Why a subagent?** The scanning workflow is long — it runs shell scripts, waits for Apify, reads large JSON files. A subagent handles this without cluttering the main conversation context. It runs its own tool calls independently.

### The `meta-ads-analyst` subagent

Defined in `.claude/agents/meta-ads-analyst.md`. It has a detailed system prompt telling it:
- The exact order to run the scripts
- How to read the diff JSON
- How to write the report (opinionated analysis, not data dumps)
- What makes a good insight vs a useless data point

The subagent reads `latest_diff.json` and produces `reports/2026-05-14.md`. It's not just summarizing data — it's interpreting it. "Palitra L's 57-day subscription explainer is almost certainly profitable" is an inference, not a raw data point.

---

## Piece 4 — The Google Sheet

The sheet has one tab: `raw_ads`. Every row is one ad. Every ad that every competitor has ever run (since your first scan) lives here permanently.

**Columns (18 total):**

| Column | What it means |
|---|---|
| `ad_id` | Meta's unique ID for the ad — permanent identifier |
| `advertiser_slug` | Your internal name (biblusi, palitra-l, etc.) |
| `page_id` | Meta's numeric page ID |
| `page_name` | The Facebook page display name |
| `first_seen` | Date this ad first appeared in a scan |
| `last_seen` | Date of the most recent scan where this ad appeared |
| `is_active` | TRUE if active in the most recent scan, FALSE if retired |
| `start_date` | When the advertiser started running this ad |
| `end_date` | When they stopped (blank if still running) |
| `ad_format` | image / carousel / video / unknown |
| `title` | Headline text |
| `body_text` | Body copy of the ad |
| `cta_text` | Call-to-action button text (Shop now, Send message, etc.) |
| `landing_url` | Where the ad sends the user |
| `publisher_platforms` | Facebook, Instagram, Messenger, WhatsApp, Threads |
| `creative_urls` | Direct links to ad images/videos |
| `is_ai_generated` | Meta's label (not exposed by the scraper, always blank) |
| `run_duration_days` | How many days the ad has been / was running |

**Why append-only?** If you overwrite rows every scan, you lose history. With append-only you can answer questions like "how long did Biblusi run their summer promotion last year?" because that data never gets deleted.

---

## Piece 5 — The Intelligence Report

The report (`reports/2026-05-14.md`) is the product — everything else is infrastructure to produce it.

**What makes a good report:** It doesn't list all 126 ads. It identifies what changed, what's working, and what you should study. The sheet is for data. The report is for decisions.

From the first scan (May 14, 2026), the key findings were:
- **Biblusi launched Litbox** — a subscription box product (books + lifestyle items), 3 ads live, celebrity endorsement, direct competitor to Palitra L's subscription funnel
- **Palitra L's 57-day subscription explainer** — most durable ad in the competitive set, educational hook ("how to sign up") rather than a discount — at 57 days, it's almost certainly profitable
- **Intelekti's 5-hour flash sale** — every book for 15 GEL, 15:00–20:00 only — time-gating mechanic nobody else uses
- **Artanuji is entirely dark** — zero active ads, all 7 "new" entries are historical 2021–2024 ads surfacing for the first time
- **100% Georgian-language copy** across all 6 competitors — no English

---

## Piece 6 — The HTML Dashboard

`reports/dashboard.html` is a standalone visual summary of the first scan's findings. It requires no server to build — it's static HTML/CSS — but needs to be served to a browser rather than opened as a file (because of font loading from Google Fonts).

In GitHub Codespaces, we serve it with Python's built-in HTTP server:
```bash
python -m http.server 8080 &
```

Then make the port public:
```bash
gh codespace ports visibility 8080:public
```

The URL follows this pattern:
```
https://{CODESPACE_NAME}-8080.app.github.dev/reports/dashboard.html
```

---

## The Full Setup — Step by Step

### Prerequisites
- An Apify account with a token (`apify.com/account/integrations`)
- A Google Sheet with a service account that has Editor permission
- A service account JSON credentials file
- Python 3.9+

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure environment
```bash
cp .env.example .env
# Edit .env and fill in:
# APIFY_TOKEN=your_token_here
# GOOGLE_SHEET_ID=your_sheet_id_here
# GOOGLE_SERVICE_ACCOUNT_JSON=./competitor-analyst-xxxxx.json
```

### 3. Add competitors to config
Edit `config/advertisers.yaml`. Each entry needs at minimum a `slug`, `name`, and `page_url`.

### 4. Resolve page IDs (one time only)
```bash
python scripts/fetch_ads.py --resolve-only
```
Copy the printed IDs into `advertisers.yaml` as quoted strings.

### 5. Run first scan
```bash
python scripts/fetch_ads.py
python scripts/update_sheet.py
python scripts/format_sheet.py   # optional — applies visual formatting
```

### 6. From then on, just use `/scan`
```
/scan                    # all competitors
/scan biblusi diogene    # specific ones
```

---

## Key Design Decisions

**Page IDs over page URLs** — IDs are permanent. URLs are not.

**Append-only sheet** — Never lose history. The whole value is the longitudinal record.

**Diff JSON as analyst input** — The subagent reads a compact summary, not 126 raw ad objects. This keeps the AI's context focused on what changed, not everything that exists.

**Opinionated reports** — The sheet shows data. The report makes arguments. "This ad is probably profitable" is more useful than "this ad has run 57 days."

**Apify over DIY** — Outsource the part that breaks when Meta changes things. Own the part that interprets the data.

---

## Concepts Glossary

**Apify** — A cloud platform for web scraping. You call their API, they run a headless browser in the cloud, you get back structured data.

**Headless browser** — A real web browser (Chrome/Firefox) running without a visible window, controlled by code. Needed when a website requires JavaScript to render its content.

**Actor** — Apify's term for a scraper module. `apify/facebook-ads-scraper` is the actor we use.

**Page ID** — Meta's permanent numeric identifier for a Facebook page. Never changes even if the page renames itself.

**MCP (Model Context Protocol)** — A protocol that lets Claude connect to external tools and services. `playwright-mcp` would let Claude control a browser directly, but for an automated pipeline Apify is more reliable.

**Subagent** — A separate Claude instance spawned to handle a specific task. The `meta-ads-analyst` subagent runs the full scan workflow and writes the report without interrupting your main conversation.

**Diff** — In this context, `latest_diff.json` — a structured summary of what changed between the last scan and this one. New ads, retired ads, still-running ads, per advertiser.

**Service account** — A non-human Google account used for server-to-server API access. We use it so the Python scripts can read/write the Google Sheet without you logging in.
