# Meta Ads Competitor Tracker

Tracks named competitors in the Georgian publishing market via Meta Ad Library.
On-demand scans triggered by `/scan`. Results land in Google Sheets and a
dated markdown report.

## Project layout

```
.
├── .claude/
│   ├── agents/meta-ads-analyst.md   # subagent that runs the workflow
│   └── commands/scan.md             # /scan slash command
├── config/
│   └── advertisers.yaml             # competitors to track
├── scripts/
│   ├── fetch_ads.py                 # Apify → data/{timestamp}.json
│   └── update_sheet.py              # JSON → Google Sheet + diff
├── data/                            # raw fetch results, gitignored
└── reports/                         # generated markdown reports
```

## How a scan works

1. `/scan` invokes the `meta-ads-analyst` subagent.
2. Subagent runs `scripts/fetch_ads.py` → writes `data/{timestamp}.json`.
3. Subagent runs `scripts/update_sheet.py` → appends to Sheet, writes
   `data/latest_diff.json` containing: new ads, retired ads, still-running ads.
4. Subagent reads the diff, classifies new ads (hook, format, theme), writes
   `reports/{date}.md` with opinionated analysis.

## Conventions

- **Append-only sheet history.** Never edit past rows. `first_seen` is locked
  on first observation; `last_seen` updates on every scan an ad is observed in.
- **Page IDs are source of truth.** Names change, IDs don't. Resolve once,
  store in `config/advertisers.yaml`.
- **Reports are dated, not overwritten.** `reports/2026-05-14.md`, never
  `reports/latest.md`.
- **Reports are opinionated.** Don't list data — the sheet does that.
  Identify what changed, what's working, what to study.

## Environment

Required env vars (see `.env.example`):
- `APIFY_TOKEN` — from apify.com/account/integrations
- `GOOGLE_SHEET_ID` — the spreadsheet ID from the Sheet URL
- `GOOGLE_SERVICE_ACCOUNT_JSON` — path to service account credentials file

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env  # fill in values
# Share your Google Sheet with the service account email (Editor permission)
```

## Running

From inside Claude Code:
```
/scan                          # scan all configured advertisers
/scan biblusi diogene          # scan a subset by name
```

## Adding a new competitor

1. Open Meta Ad Library, search the brand, copy their Page URL.
2. Add an entry to `config/advertisers.yaml` with name and page_url.
3. Run `python scripts/fetch_ads.py --resolve-only` to find the Page ID.
4. Commit the populated entry.

## Country scope

All scans default to `country=GE` (Georgia). Override via
`/scan --country=US` if needed for research.
