#!/usr/bin/env python3
"""
update_sheet.py — Sync the latest scan to Google Sheets and compute a diff.

Reads the most recent data/*.json, reads existing rows from the Sheet,
upserts (append new ads, update last_seen on still-running ads, mark retired),
then writes data/latest_diff.json for the analyst subagent to consume.

Sheet schema — tab "raw_ads":
    ad_id | advertiser_slug | page_id | page_name | first_seen | last_seen |
    is_active | start_date | end_date | ad_format | title | body_text |
    cta_text | landing_url | publisher_platforms | creative_urls |
    is_ai_generated | run_duration_days

The Sheet is the source of truth for history. Local JSON files are caches.

Env:
    GOOGLE_SHEET_ID
    GOOGLE_SERVICE_ACCOUNT_JSON (path to credentials file)
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

import gspread
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

TAB_NAME = "raw_ads"
HEADERS = [
    "ad_id", "advertiser_slug", "page_id", "page_name",
    "first_seen", "last_seen", "is_active",
    "start_date", "end_date", "ad_format",
    "title", "body_text", "cta_text", "landing_url",
    "publisher_platforms", "creative_urls",
    "is_ai_generated", "run_duration_days",
]


def latest_scan_path() -> Path:
    scans = sorted(DATA_DIR.glob("*.json"))
    scans = [p for p in scans if not p.name.startswith("latest_")]
    if not scans:
        sys.exit("ERROR: no scan files found in data/. Run fetch_ads.py first.")
    return scans[-1]


def open_sheet():
    sa_path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sa_path or not sheet_id:
        sys.exit("ERROR: GOOGLE_SERVICE_ACCOUNT_JSON and GOOGLE_SHEET_ID must be set.")
    client = gspread.service_account(filename=sa_path)
    book = client.open_by_key(sheet_id)
    try:
        tab = book.worksheet(TAB_NAME)
    except gspread.WorksheetNotFound:
        tab = book.add_worksheet(title=TAB_NAME, rows=1000, cols=len(HEADERS))
        tab.append_row(HEADERS)
    return tab


def read_existing(tab) -> dict[str, dict]:
    """Return {ad_id: row_dict} for all existing rows. Each dict includes _row (1-based sheet row)."""
    records = tab.get_all_records()
    result = {}
    for i, r in enumerate(records, start=2):  # row 1 = header, data starts at row 2
        if r.get("ad_id"):
            r["_row"] = i
            result[str(r["ad_id"])] = r
    return result


def compute_run_duration(start: str | None, end: str | None, today: date) -> int | None:
    if not start:
        return None
    try:
        s = datetime.strptime(start[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    end_date = today
    if end:
        try:
            end_date = datetime.strptime(end[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
    return (end_date - s).days


def ad_to_row(ad: dict, first_seen: str, last_seen: str, today: date) -> list:
    run_days = compute_run_duration(ad.get("start_date"), ad.get("end_date"), today)
    return [
        ad.get("ad_id") or "",
        ad.get("advertiser_slug") or "",
        ad.get("page_id") or "",
        ad.get("page_name") or "",
        first_seen,
        last_seen,
        bool(ad.get("is_active")),
        ad.get("start_date") or "",
        ad.get("end_date") or "",
        ad.get("ad_format") or "",
        (ad.get("title") or "")[:500],
        (ad.get("body_text") or "")[:2000],
        ad.get("cta_text") or "",
        ad.get("landing_url") or "",
        ", ".join(ad.get("publisher_platforms") or []) if isinstance(ad.get("publisher_platforms"), list) else str(ad.get("publisher_platforms") or ""),
        ", ".join(ad.get("creative_urls") or [])[:1000],
        bool(ad.get("is_ai_generated")),
        run_days if run_days is not None else "",
    ]


def main():
    load_dotenv(ROOT / ".env")

    scan_path = latest_scan_path()
    scan = json.loads(scan_path.read_text(encoding="utf-8"))
    scan_date = scan["scan_date"]
    today = datetime.strptime(scan_date, "%Y-%m-%d").date()

    print(f"Updating sheet from scan: {scan_path.name}")
    print(f"  {scan['ad_count']} ads across {len(scan['advertisers_scanned'])} advertisers")

    tab = open_sheet()
    existing = read_existing(tab)
    print(f"  existing sheet rows: {len(existing)}")

    seen_in_this_scan = set()
    new_rows = []
    updates = []  # (row_index, last_seen) tuples

    for ad in scan["ads"]:
        ad_id = str(ad.get("ad_id") or "")
        if not ad_id:
            continue
        seen_in_this_scan.add(ad_id)

        if ad_id in existing:
            # Still running — update last_seen
            updates.append((existing[ad_id]["_row"], scan_date))
        else:
            # New ad
            new_rows.append(ad_to_row(ad, scan_date, scan_date, today))

    # Identify retired ads — were in sheet, scanned advertisers, but not seen now
    scanned_slugs = set(scan["advertisers_scanned"])
    retired_ids = [
        ad_id for ad_id, row in existing.items()
        if row.get("advertiser_slug") in scanned_slugs and ad_id not in seen_in_this_scan
        and row.get("is_active") is True
    ]

    # Apply writes
    if new_rows:
        tab.append_rows(new_rows, value_input_option="USER_ENTERED")
        print(f"  appended {len(new_rows)} new rows")
    if updates:
        # Batch update column F (last_seen, 6th column)
        cells = [gspread.Cell(row=row_idx, col=6, value=last_seen) for row_idx, last_seen in updates]
        tab.update_cells(cells, value_input_option="USER_ENTERED")
        print(f"  updated last_seen on {len(updates)} rows")
    if retired_ids:
        # Flip is_active to FALSE (col 7) for ads not seen in this scan
        retired_cells = [
            gspread.Cell(row=existing[ad_id]["_row"], col=7, value=False)
            for ad_id in retired_ids
        ]
        tab.update_cells(retired_cells, value_input_option="USER_ENTERED")
        print(f"  flagged {len(retired_ids)} ads as retired")

    # Build the diff payload for the analyst
    diff = build_diff(scan, existing, seen_in_this_scan, retired_ids, today)
    diff_path = DATA_DIR / "latest_diff.json"
    diff_path.write_text(json.dumps(diff, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  wrote diff to {diff_path.relative_to(ROOT)}")


def build_diff(scan, existing, seen_in_this_scan, retired_ids, today) -> dict:
    by_adv: dict[str, dict] = {}
    today_d = today

    for ad in scan["ads"]:
        slug = ad.get("advertiser_slug") or "unknown"
        if slug not in by_adv:
            by_adv[slug] = {
                "new_ads": [],
                "retired_ads": [],
                "still_running": [],
                "total_active": 0,
                "refresh_rate_30d": 0,
                "longest_running_days": 0,
            }
        ad_id = str(ad.get("ad_id") or "")
        is_new = ad_id not in existing
        run_days_raw = compute_run_duration(ad.get("start_date"), ad.get("end_date"), today_d)
        run_days = run_days_raw if run_days_raw is not None else 0

        record = {
            "ad_id": ad_id,
            "ad_format": ad.get("ad_format"),
            "title": ad.get("title"),
            "body_text": (ad.get("body_text") or "")[:500],
            "cta_text": ad.get("cta_text"),
            "landing_url": ad.get("landing_url"),
            "start_date": ad.get("start_date"),
            "end_date": ad.get("end_date"),
            "run_duration_days": run_days_raw,  # None when start_date missing
            "creative_urls": (ad.get("creative_urls") or [])[:3],
            "publisher_platforms": ad.get("publisher_platforms"),
            "is_ai_generated": ad.get("is_ai_generated"),
        }
        if is_new:
            by_adv[slug]["new_ads"].append(record)
            if run_days_raw is not None and run_days_raw <= 30:
                by_adv[slug]["refresh_rate_30d"] += 1
        else:
            by_adv[slug]["still_running"].append(record)

        if ad.get("is_active"):
            by_adv[slug]["total_active"] += 1
        by_adv[slug]["longest_running_days"] = max(
            by_adv[slug]["longest_running_days"], run_days
        )

    for ad_id in retired_ids:
        row = existing[ad_id]
        slug = row.get("advertiser_slug") or "unknown"
        if slug not in by_adv:
            by_adv[slug] = {
                "new_ads": [], "retired_ads": [], "still_running": [],
                "total_active": 0, "refresh_rate_30d": 0, "longest_running_days": 0,
            }
        by_adv[slug]["retired_ads"].append({
            "ad_id": ad_id,
            "title": row.get("title"),
            "body_text": (row.get("body_text") or "")[:500],
            "ad_format": row.get("ad_format"),
            "first_seen": row.get("first_seen"),
            "last_seen_before_retire": row.get("last_seen"),
        })

    return {
        "scan_date": scan["scan_date"],
        "country": scan["country"],
        "by_advertiser": by_adv,
    }


if __name__ == "__main__":
    main()
