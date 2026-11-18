
import os
import json
import time
import requests

from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# ==========================================================
# AI FOOTBALL PREDICTION V3.3B.1
# API-FOOTBALL DATE-BASED ODDS COLLECTOR
# ==========================================================

API_KEY = os.getenv("API_FOOTBALL_KEY")
BASE_URL = "https://v3.football.api-sports.io"

TIMEZONE = "Asia/Ho_Chi_Minh"
TZ = ZoneInfo(TIMEZONE)

OUTPUT_DIR = Path("data/public_odds")

# API FOOTBALL FREE PLAN: MAX PAGE = 3
MAX_ODDS_PAGES = 3

REQUEST_DELAY = 1.5
TIMEOUT = 30

session = requests.Session()
session.headers.update({
    "x-apisports-key": API_KEY or ""
})


def now_local():
    return datetime.now(TZ)


def api_get(endpoint, params):
    response = session.get(
        f"{BASE_URL}/{endpoint}",
        params=params,
        timeout=TIMEOUT
    )

    print(
        f"HTTP {response.status_code} | "
        f"{endpoint} | params={params}"
    )

    remaining = response.headers.get(
        "x-ratelimit-requests-remaining"
    )
    limit = response.headers.get(
        "x-ratelimit-requests-limit"
    )

    if remaining is not None:
        print(f"API quota remaining: {remaining}/{limit}")

    if response.status_code in (401, 403):
        raise RuntimeError(
            f"API authorization failed: HTTP {response.status_code}"
        )

    response.raise_for_status()

    payload = response.json()

    if payload.get("errors"):
        raise RuntimeError(
            f"API returned errors: {payload['errors']}"
        )

    return payload


# ==========================================================
# 1. COLLECT FIXTURES
# ==========================================================

def collect_fixtures(date_string):
    print("\n[1] COLLECTING FIXTURES")

    payload = api_get(
        "fixtures",
        {
            "date": date_string,
            "timezone": TIMEZONE
        }
    )

    fixtures = payload.get("response", [])

    print("Fixtures received:", len(fixtures))

    return fixtures


# ==========================================================
# 2. BUILD FIXTURE INDEX
# ==========================================================

def build_fixture_index(fixtures):
    index = {}

    for item in fixtures:
        fixture = item.get("fixture", {})
        teams = item.get("teams", {})
        league = item.get("league", {})

        fixture_id = fixture.get("id")

        if fixture_id is None:
            continue

        index[str(fixture_id)] = {
            "fixture_id": fixture_id,
            "date": fixture.get("date"),
            "timestamp": fixture.get("timestamp"),
            "status": fixture.get("status", {}),
            "league": {
                "id": league.get("id"),
                "name": league.get("name"),
                "country": league.get("country"),
                "season": league.get("season"),
                "round": league.get("round")
            },
            "home": teams.get("home", {}).get("name"),
            "away": teams.get("away", {}).get("name")
        }

    return index


# ==========================================================
# 3. COLLECT ODDS BY DATE
# ==========================================================

def collect_odds_by_date(date_string):
    print("\n[2] COLLECTING ODDS BY DATE")
    print("IMPORTANT: season parameter is NOT used.")
    print("Free plan maximum pages:", MAX_ODDS_PAGES)

    all_odds = []
    total_pages = None

    for page in range(1, MAX_ODDS_PAGES + 1):

        print(f"\nOdds page {page}/{MAX_ODDS_PAGES}")

        payload = api_get(
            "odds",
            {
                "date": date_string,
                "timezone": TIMEZONE,
                "page": page
            }
        )

        response = payload.get("response", [])
        paging = payload.get("paging", {})

        if total_pages is None:
            total_pages = paging.get("total", 1)

        print("Records on page:", len(response))
        print("Provider total pages:", total_pages)

        all_odds.extend(response)

        if page >= total_pages:
            break

        if page < MAX_ODDS_PAGES:
            time.sleep(REQUEST_DELAY)

    if total_pages and total_pages > MAX_ODDS_PAGES:
        print(
            "\nWARNING: Free plan page limit reached."
        )
        print("Available pages:", MAX_ODDS_PAGES)
        print("Provider total pages:", total_pages)
        print("Collected data is PARTIAL.")

    print("\nTotal raw odds records:", len(all_odds))

    return all_odds, total_pages


# ==========================================================
# 4. FILTER AND LINK UPCOMING ODDS
# ==========================================================

def link_upcoming_odds(raw_odds, fixture_index):
    print("\n[3] LINKING ODDS TO FIXTURES")

    current_timestamp = int(time.time())

    linked = []
    unmatched = 0
    expired = 0

    for item in raw_odds:
        fixture_id = item.get("fixture", {}).get("id")

        if fixture_id is None:
            unmatched += 1
            continue

        fixture_info = fixture_index.get(str(fixture_id))

        if fixture_info is None:
            unmatched += 1
            continue

        kickoff = fixture_info.get("timestamp")

        if not kickoff or kickoff <= current_timestamp:
            expired += 1
            continue

        status = fixture_info.get("status", {})
        short_status = status.get("short")

        if short_status not in ("NS", "TBD"):
            expired += 1
            continue

        linked.append({
            "fixture_id": fixture_id,
            "fixture_info": fixture_info,
            "bookmakers": item.get("bookmakers", [])
        })

    print("Linked upcoming matches:", len(linked))
    print("Unmatched odds records:", unmatched)
    print("Excluded finished/live matches:", expired)

    return linked


# ==========================================================
# 5. SAVE DATA
# ==========================================================

def save_data(date_string, fixtures, raw_odds, linked, total_pages):
    print("\n[4] SAVING DATA")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    date_dir = OUTPUT_DIR / date_string
    date_dir.mkdir(parents=True, exist_ok=True)

    started_at = now_local().isoformat()

    bookmaker_count = sum(
        len(item.get("bookmakers", []))
        for item in linked
    )

    data = {
        "engine": "AI FOOTBALL PREDICTION V3.3B.1",
        "source": "API-FOOTBALL",
        "query_method": "DATE_BASED",
        "date": date_string,
        "timezone": TIMEZONE,
        "started_at": started_at,
        "finished_at": now_local().isoformat(),

        "configuration": {
            "max_odds_pages": MAX_ODDS_PAGES,
            "provider_total_pages": total_pages,
            "season_parameter_used": False,
            "partial_collection": (
                total_pages is not None
                and total_pages > MAX_ODDS_PAGES
            )
        },

        "summary": {
            "fixtures_received": len(fixtures),
            "raw_odds_records": len(raw_odds),
            "linked_upcoming_matches": len(linked),
            "bookmaker_records": bookmaker_count
        },

        "odds": linked
    }

    latest_file = date_dir / "latest.json"

    timestamp = now_local().strftime("%Y%m%d_%H%M%S")
    archive_file = date_dir / f"odds_{timestamp}.json"

    with open(latest_file, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)

    with open(archive_file, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)

    print("Latest file:", latest_file)
    print("Archive file:", archive_file)

    return data


# ==========================================================
# 6. MAIN
# ==========================================================

def main():
    print("=" * 60)
    print("AI FOOTBALL PREDICTION V3.3B.1")
    print("API-FOOTBALL DATE-BASED ODDS COLLECTOR")
    print("=" * 60)

    if not API_KEY:
        raise RuntimeError(
            "Missing API_FOOTBALL_KEY GitHub Secret."
        )

    date_string = now_local().strftime("%Y-%m-%d")

    print("Vietnam date:", date_string)
    print("Timezone:", TIMEZONE)

    fixtures = collect_fixtures(date_string)

    fixture_index = build_fixture_index(fixtures)

    raw_odds, total_pages = collect_odds_by_date(
        date_string
    )

    linked = link_upcoming_odds(
        raw_odds,
        fixture_index
    )

    data = save_data(
        date_string,
        fixtures,
        raw_odds,
        linked,
        total_pages
    )

    print("\n" + "=" * 60)
    print("COLLECTION COMPLETED")
    print("=" * 60)

    print(
        "Fixtures:",
        data["summary"]["fixtures_received"]
    )

    print(
        "Raw odds:",
        data["summary"]["raw_odds_records"]
    )

    print(
        "Upcoming matches:",
        data["summary"]["linked_upcoming_matches"]
    )

    print(
        "Bookmaker records:",
        data["summary"]["bookmaker_records"]
    )

    print(
        "Partial collection:",
        data["configuration"]["partial_collection"]
    )

    print("\nNext step: Run v33_engine.py")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("\nCOLLECTION FAILED:")
        print(error)
        raise
