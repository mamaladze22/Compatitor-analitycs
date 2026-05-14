#!/usr/bin/env python3
"""
fetch_ads.py — Fetch Meta Ad Library results for tracked advertisers via Apify.

Reads config/advertisers.yaml, runs the Apify actor for each advertiser with a
populated page_id (or page_url), normalizes the output schema, and writes a
single timestamped JSON file under data/.

Usage:
    python scripts/fetch_ads.py                       # all advertisers
    python scripts/fetch_ads.py biblusi diogene       # subset by slug
    python scripts/fetch_ads.py --country=US          # override country
    python scripts/fetch_ads.py --resolve-only        # resolve page_ids only
    python scripts/fetch_ads.py --dry-run             # don't call Apify

Env:
    APIFY_TOKEN must be set (or in .env loaded by python-dotenv).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from apify_client import ApifyClient
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "advertisers.yaml"
DATA_DIR = ROOT / "data"

# apify/facebook-ads-scraper: Apify's official actor, 21k users, no FB token needed.
# Accepts Facebook Page URLs and Ad Library URLs via startUrls.
# Output: snapshot.{body.text, title, ctaText, ctaType, linkUrl, cards[]}.
ACTOR_SLUG = "apify/facebook-ads-scraper"


def load_config() -> dict:
    with CONFIG_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def filter_advertisers(config: dict, requested_slugs: list[str]) -> list[dict]:
    all_advertisers = config["advertisers"]
    if not requested_slugs:
        return all_advertisers
    requested = set(s.lower() for s in requested_slugs)
    matched = [a for a in all_advertisers if a["slug"] in requested]
    unmatched = requested - {a["slug"] for a in matched}
    if unmatched:
        print(
            f"WARNING: unknown advertiser slugs: {sorted(unmatched)}",
            file=sys.stderr,
        )
    return matched


def build_actor_input(advertisers: list[dict], country: str, max_per_adv: int) -> dict:
    """
    Build the actor input for apify/facebook-ads-scraper.

    startUrls accepts either:
    - An Ad Library URL with view_all_page_id (preferred when page_id is known)
    - A plain Facebook Page URL (fallback; actor resolves the page itself)

    resultsLimit applies per input URL. activeStatus "" means all ads (active + inactive).
    """
    start_urls = []
    for adv in advertisers:
        if adv.get("page_id"):
            url = (
                "https://www.facebook.com/ads/library/"
                f"?active_status=all&ad_type=all&country={country}"
                f"&view_all_page_id={adv['page_id']}"
            )
        elif adv.get("page_url"):
            url = adv["page_url"]
        else:
            print(f"WARNING: skipping {adv['slug']} — no page_id or page_url", file=sys.stderr)
            continue
        start_urls.append({"url": url})

    return {
        "startUrls": start_urls,
        "resultsLimit": max_per_adv,  # per URL
        "activeStatus": "",           # "" = all statuses (active + inactive)
    }


def normalize_ad(raw: dict, advertiser_slug: str, scan_date: str) -> dict:
    """
    Normalize apify/facebook-ads-scraper output into our canonical schema.

    Key field locations in this actor's output:
    - Ad text, title, CTA, URL live under raw["snapshot"]
    - Cards (carousel/video) live under raw["snapshot"]["cards"]
    - Dates are startDateFormatted / endDateFormatted (ISO-like strings)
    """
    snap = raw.get("snapshot") if isinstance(raw.get("snapshot"), dict) else {}
    snap_body = snap.get("body") if isinstance(snap.get("body"), dict) else {}

    return {
        "scan_date": scan_date,
        "advertiser_slug": advertiser_slug,
        "ad_id": raw.get("adArchiveID") or raw.get("adArchiveId"),
        "page_id": str(raw.get("pageID") or raw.get("pageId") or ""),
        "page_name": raw.get("pageName"),
        "start_date": raw.get("startDateFormatted"),
        "end_date": raw.get("endDateFormatted"),
        "is_active": raw.get("isActive"),
        "publisher_platforms": raw.get("publisherPlatform") or [],
        "ad_format": _infer_format(snap),
        "body_text": snap_body.get("text") or snap.get("caption"),
        "title": snap.get("title"),
        "cta_text": snap.get("ctaText") or snap.get("ctaType"),
        "landing_url": snap.get("linkUrl"),
        "creative_urls": _extract_creative_urls(snap),
        "currency": raw.get("currency"),
        "spend": raw.get("spend"),        # only populated for EU/political
        "impressions": raw.get("impressionsText"),
        "is_ai_generated": None,          # not exposed by this actor
        "_raw_keys": sorted(raw.keys()),  # debug aid — remove after first live run
    }


def _infer_format(snap: dict) -> str:
    """Infer ad format from the snapshot dict."""
    cards = snap.get("cards") or []
    if not cards:
        if snap.get("videoHdUrl") or snap.get("videoSdUrl"):
            return "video"
        if snap.get("originalImageUrl") or snap.get("resizedImageUrl"):
            return "image"
        return "unknown"
    has_video = any(
        c.get("videoHdUrl") or c.get("videoSdUrl")
        for c in cards
        if isinstance(c, dict)
    )
    if has_video:
        return "video"
    return "carousel" if len(cards) > 1 else "image"


def _extract_creative_urls(snap: dict) -> list[str]:
    urls = []
    cards = snap.get("cards") or []
    for card in cards:
        if not isinstance(card, dict):
            continue
        for key in ("originalImageUrl", "resizedImageUrl", "videoHdUrl", "videoSdUrl"):
            val = card.get(key)
            if val:
                urls.append(val)
    # Top-level snapshot image/video (single-creative ads)
    for key in ("originalImageUrl", "resizedImageUrl", "videoHdUrl", "videoSdUrl"):
        val = snap.get(key)
        if val and val not in urls:
            urls.append(val)
    return urls


def run_scan(advertisers: list[dict], country: str, max_per_adv: int, dry_run: bool) -> dict:
    scan_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    scan_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")

    actor_input = build_actor_input(advertisers, country, max_per_adv)

    if dry_run:
        print("DRY RUN — would call actor with input:")
        print(json.dumps(actor_input, indent=2))
        return {"scan_date": scan_date, "ads": [], "advertisers_scanned": []}

    token = os.environ.get("APIFY_TOKEN")
    if not token:
        sys.exit("ERROR: APIFY_TOKEN not set. See .env.example.")

    client = ApifyClient(token=token)
    print(f"Starting Apify actor {ACTOR_SLUG} for {len(actor_input['startUrls'])} URLs...")
    run = client.actor(ACTOR_SLUG).call(run_input=actor_input)
    print(f"Actor run finished: {run['id']} (status: {run['status']})")

    # Build a map of page_id -> slug for tagging
    page_to_slug = {str(a["page_id"]): a["slug"] for a in advertisers if a.get("page_id")}
    name_to_slug = {a["name"].lower(): a["slug"] for a in advertisers}

    ads = []
    for raw in client.dataset(run["defaultDatasetId"]).iterate_items():
        page_id = str(raw.get("pageID") or raw.get("pageId") or "")
        page_name = (raw.get("pageName") or "").lower()
        slug = page_to_slug.get(page_id) or name_to_slug.get(page_name) or "unknown"
        ads.append(normalize_ad(raw, slug, scan_date))

    output = {
        "scan_date": scan_date,
        "scan_timestamp": scan_ts,
        "country": country,
        "actor": ACTOR_SLUG,
        "actor_run_id": run["id"],
        "advertisers_scanned": [a["slug"] for a in advertisers],
        "ad_count": len(ads),
        "ads": ads,
    }

    DATA_DIR.mkdir(exist_ok=True)
    out_path = DATA_DIR / f"{scan_ts}.json"
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(ads)} ads to {out_path.relative_to(ROOT)}")
    return output


def resolve_page_ids():
    """
    For each advertiser missing a page_id, run the actor against the page_url
    and extract the pageID from the first result.
    """
    config = load_config()
    needs_resolve = [a for a in config["advertisers"] if not a.get("page_id")]
    if not needs_resolve:
        print("All advertisers already have page_id resolved.")
        return

    print(f"Resolving {len(needs_resolve)} page IDs...")
    print("(This calls Apify with a small query per advertiser — cost ~$0.001 each.)")

    token = os.environ.get("APIFY_TOKEN")
    if not token:
        sys.exit("ERROR: APIFY_TOKEN not set.")

    client = ApifyClient(token=token)
    resolved = {}

    for adv in needs_resolve:
        if adv.get("page_url"):
            actor_url = adv["page_url"]
        else:
            actor_url = (
                "https://www.facebook.com/ads/library/"
                f"?active_status=all&ad_type=all&country=GE"
                f"&q={adv['name'].replace(' ', '%20')}&search_type=keyword_unordered"
            )
        actor_input = {
            "startUrls": [{"url": actor_url}],
            "resultsLimit": 5,
            "activeStatus": "active",
        }
        run = client.actor(ACTOR_SLUG).call(run_input=actor_input)
        for item in client.dataset(run["defaultDatasetId"]).iterate_items():
            page_id = item.get("pageID") or item.get("pageId")
            if page_id:
                resolved[adv["slug"]] = str(page_id)
                print(f"  {adv['slug']:15} → {page_id}")
                break
        else:
            print(f"  {adv['slug']:15} → NOT FOUND")

    if resolved:
        print("\nAdd these to config/advertisers.yaml:")
        for slug, pid in resolved.items():
            print(f'  {slug}: page_id: "{pid}"')


def main():
    load_dotenv(ROOT / ".env")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slugs", nargs="*", help="Advertiser slugs (default: all)")
    parser.add_argument("--country", default=None, help="Country code (default from config)")
    parser.add_argument("--resolve-only", action="store_true", help="Just resolve page_ids and exit")
    parser.add_argument("--dry-run", action="store_true", help="Don't call Apify, print the payload")
    args = parser.parse_args()

    if args.resolve_only:
        resolve_page_ids()
        return

    config = load_config()
    advertisers = filter_advertisers(config, args.slugs)
    if not advertisers:
        sys.exit("ERROR: no advertisers selected.")

    country = args.country or config["defaults"]["country"]
    max_per_adv = config["defaults"]["max_ads_per_advertiser"]

    run_scan(advertisers, country, max_per_adv, args.dry_run)


if __name__ == "__main__":
    main()
