#!/usr/bin/env python3
"""
format_sheet.py — Apply professional formatting to the raw_ads Google Sheet tab.

Run once after first populate, or any time you want to re-apply styles.
Safe to run repeatedly — formatting is idempotent.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import gspread
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
TAB_NAME = "raw_ads"

# Column widths in pixels (18 columns: A–R)
COLUMN_WIDTHS = {
    0:  140,  # ad_id
    1:  110,  # advertiser_slug
    2:  130,  # page_id
    3:  130,  # page_name
    4:   95,  # first_seen
    5:   95,  # last_seen
    6:   75,  # is_active
    7:   95,  # start_date
    8:   95,  # end_date
    9:   85,  # ad_format
    10: 220,  # title
    11: 320,  # body_text
    12: 100,  # cta_text
    13: 220,  # landing_url
    14: 130,  # publisher_platforms
    15: 200,  # creative_urls
    16:  95,  # is_ai_generated
    17: 110,  # run_duration_days
}

# Brand colors
HEADER_BG    = {"red": 0.106, "green": 0.149, "blue": 0.278}  # deep navy #1B2647
HEADER_FG    = {"red": 1.0,   "green": 1.0,   "blue": 1.0}    # white
ROW_ALT      = {"red": 0.937, "green": 0.945, "blue": 0.969}  # very light blue-grey
ROW_BASE     = {"red": 1.0,   "green": 1.0,   "blue": 1.0}    # white
GREEN_BG     = {"red": 0.827, "green": 0.937, "blue": 0.800}  # soft green for TRUE
RED_BG       = {"red": 0.988, "green": 0.855, "blue": 0.855}  # soft red for FALSE


def rgb(r, g, b):
    return {"red": r / 255, "green": g / 255, "blue": b / 255}


def main():
    load_dotenv(ROOT / ".env")
    sa_path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sa_path or not sheet_id:
        sys.exit("ERROR: GOOGLE_SERVICE_ACCOUNT_JSON and GOOGLE_SHEET_ID must be set.")

    client = gspread.service_account(filename=sa_path)
    book = client.open_by_key(sheet_id)
    try:
        tab = book.worksheet(TAB_NAME)
    except gspread.WorksheetNotFound:
        sys.exit(f"ERROR: tab '{TAB_NAME}' not found. Run update_sheet.py first.")

    gid = tab.id
    num_cols = len(COLUMN_WIDTHS)
    num_rows = tab.row_count

    print(f"Formatting '{TAB_NAME}' (gid={gid}, {num_cols} columns, {num_rows} rows)…")

    requests = []

    # 1. Freeze header row + first column
    requests.append({
        "updateSheetProperties": {
            "properties": {
                "sheetId": gid,
                "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": 1},
            },
            "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
        }
    })

    # 2. Header row — bold, white text, navy background, center-align
    requests.append({
        "repeatCell": {
            "range": {"sheetId": gid, "startRowIndex": 0, "endRowIndex": 1,
                      "startColumnIndex": 0, "endColumnIndex": num_cols},
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": HEADER_BG,
                    "textFormat": {
                        "foregroundColor": HEADER_FG,
                        "bold": True,
                        "fontSize": 10,
                        "fontFamily": "Arial",
                    },
                    "horizontalAlignment": "CENTER",
                    "verticalAlignment": "MIDDLE",
                    "wrapStrategy": "CLIP",
                }
            },
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)",
        }
    })

    # 3. Data rows — font, vertical alignment, wrap strategy
    requests.append({
        "repeatCell": {
            "range": {"sheetId": gid, "startRowIndex": 1, "endRowIndex": num_rows,
                      "startColumnIndex": 0, "endColumnIndex": num_cols},
            "cell": {
                "userEnteredFormat": {
                    "textFormat": {"fontSize": 9, "fontFamily": "Arial"},
                    "verticalAlignment": "TOP",
                    "wrapStrategy": "CLIP",
                }
            },
            "fields": "userEnteredFormat(textFormat,verticalAlignment,wrapStrategy)",
        }
    })

    # 4. body_text column (index 11) — WRAP so it's readable
    requests.append({
        "repeatCell": {
            "range": {"sheetId": gid, "startRowIndex": 1, "endRowIndex": num_rows,
                      "startColumnIndex": 11, "endColumnIndex": 12},
            "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
            "fields": "userEnteredFormat.wrapStrategy",
        }
    })

    # 5. Alternating row colors (banded range)
    requests.append({
        "addBanding": {
            "bandedRange": {
                "bandedRangeId": 1,
                "range": {"sheetId": gid, "startRowIndex": 1, "endRowIndex": num_rows,
                          "startColumnIndex": 0, "endColumnIndex": num_cols},
                "rowProperties": {
                    "headerColor":      ROW_BASE,
                    "firstBandColor":   ROW_BASE,
                    "secondBandColor":  ROW_ALT,
                },
            }
        }
    })

    # 6. Column widths
    for col_idx, width_px in COLUMN_WIDTHS.items():
        requests.append({
            "updateDimensionProperties": {
                "range": {
                    "sheetId": gid,
                    "dimension": "COLUMNS",
                    "startIndex": col_idx,
                    "endIndex": col_idx + 1,
                },
                "properties": {"pixelSize": width_px},
                "fields": "pixelSize",
            }
        })

    # 7. Header row height (taller for readability)
    requests.append({
        "updateDimensionProperties": {
            "range": {"sheetId": gid, "dimension": "ROWS", "startIndex": 0, "endIndex": 1},
            "properties": {"pixelSize": 36},
            "fields": "pixelSize",
        }
    })

    # 8. Conditional formatting — is_active column (index 6)
    #    TRUE → green, FALSE → red
    for bool_val, bg in [("TRUE", GREEN_BG), ("FALSE", RED_BG)]:
        requests.append({
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{"sheetId": gid, "startRowIndex": 1, "endRowIndex": num_rows,
                                "startColumnIndex": 6, "endColumnIndex": 7}],
                    "booleanRule": {
                        "condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": bool_val}]},
                        "format": {"backgroundColor": bg},
                    },
                },
                "index": 0,
            }
        })

    # 9. Auto-filter across all header columns
    requests.append({
        "setBasicFilter": {
            "filter": {
                "range": {"sheetId": gid, "startRowIndex": 0, "endRowIndex": num_rows,
                          "startColumnIndex": 0, "endColumnIndex": num_cols},
            }
        }
    })

    # 10. Border on header row (bottom border, slightly heavier)
    requests.append({
        "updateBorders": {
            "range": {"sheetId": gid, "startRowIndex": 0, "endRowIndex": 1,
                      "startColumnIndex": 0, "endColumnIndex": num_cols},
            "bottom": {
                "style": "SOLID_MEDIUM",
                "color": {"red": 0.8, "green": 0.8, "blue": 0.8},
            },
        }
    })

    book.batch_update({"requests": requests})
    print("Done. Open the sheet and click the 'raw_ads' tab.")
    print(f"  https://docs.google.com/spreadsheets/d/{sheet_id}/edit")


if __name__ == "__main__":
    main()
