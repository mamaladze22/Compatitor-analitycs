---
description: Scan Meta Ad Library for tracked competitors, update sheet, produce report
argument-hint: [advertiser names | --country=GE]
---

Run a competitor Meta Ads scan for $ARGUMENTS.

Delegate this work to the `meta-ads-analyst` subagent. Pass the arguments
exactly as the user provided them.

If `$ARGUMENTS` is empty, scan all advertisers in `config/advertisers.yaml`.
If specific advertiser names are passed, scan only those.
If `--country=XX` is passed, use that country code (default GE).

The subagent will:
1. Fetch from Apify
2. Update the Google Sheet
3. Write a dated markdown report to `reports/`

When the subagent finishes, surface:
- The path to the new report
- A one-paragraph summary lifted from the report's TL;DR section
- The Sheet URL so the user can open it
