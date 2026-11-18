
import os
import json
import time
import requests

from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

# ============================================================
# AI FOOTBALL PREDICTION V3.4.4.2
# ADAPTIVE QUOTA + FIXTURE/ODDS MATCHING
# ============================================================

API_BASE = "https://v3.football.api-sports.io"
API_KEY = os.getenv("API_FOOTBALL_KEY", "").strip()

TIMEZONE = "Asia/Ho_Chi_Minh"
MAX_ODDS_PAGES = 3
MAX_TOTAL_REQUESTS = 4

# Giữ lại 15 request làm dự phòng
MIN_REMAINING_QUOTA = 15

REQUEST_DELAY = 1.2

VN_TZ = ZoneInfo(TIMEZONE)
RUN_TIME = datetime.now(VN_TZ)
TARGET_DATE = RUN_TIME.strftime("%Y-%m-%d")

OUTPUT_DIR = Path("data/public_odds") / TARGET_DATE
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

LATEST_FILE = OUTPUT_DIR / "latest.json"

session = requests.Session()
session.headers.update({
    "x-apisports-key": API_KEY
})

request_count = 0
api_remaining = None
api_limit = None
api_errors = []


# ============================================================
# API REQUEST
# ============================================================

def api_get(endpoint, params):
    global request_count, api_remaining, api_limit

    if request_count >= MAX_TOTAL_REQUESTS:
        raise RuntimeError("Maximum request limit reached.")

    if (
        api_remaining is not None
        and api_remaining <= MIN_REMAINING_QUOTA
    ):
        raise RuntimeError(
            f"Quota reserve reached: {api_remaining}"
        )

    response = session.get(
        f"{API_BASE}/{endpoint}",
        params=params,
        timeout=30
    )

    request_count += 1

    remaining = response.headers.get(
        "x-ratelimit-requests-remaining"
    )

    limit = response.headers.get(
        "x-ratelimit-requests-limit"
    )

    if remaining is not None:
        api_remaining = int(remaining)

    if limit is not None:
        api_limit = int(limit)

    response.raise_for_status()

    payload = response.json()

    errors = payload.get("errors")

    if errors:
        raise RuntimeError(f"API errors: {errors}")

    time.sleep(REQUEST_DELAY)

    return payload


# ============================================================
# COLLECT FIXTURES
# ============================================================

def collect_fixtures():

    print("\n[1] COLLECTING FIXTURES")
    print("Target date:", TARGET_DATE)

    payload = api_get(
        "fixtures",
        {
            "date": TARGET_DATE,
            "timezone": TIMEZONE
        }
    )

    fixtures = payload.get("response", [])

    print("Fixtures received:", len(fixtures))

    fixture_map = {}

    for item in fixtures:

        fixture = item.get("fixture") or {}
        teams = item.get("teams") or {}
        league = item.get("league") or {}

        fixture_id = fixture.get("id")

        if not fixture_id:
            continue

        home = teams.get("home") or {}
        away = teams.get("away") or {}
        status = fixture.get("status") or {}

        fixture_map[str(fixture_id)] = {
            "fixture_id": fixture_id,
            "date": fixture.get("date"),
            "timestamp": fixture.get("timestamp"),
            "status_short": status.get("short", "NS"),
            "home": home.get("name"),
            "away": away.get("name"),
            "league": league.get("name"),
            "country": league.get("country"),
            "league_id": league.get("id"),
            "season": league.get("season")
        }

    print("Valid fixture IDs:", len(fixture_map))

    return fixture_map


# ============================================================
# COLLECT ODDS
# ============================================================

def collect_odds():

    print("\n[2] COLLECTING ODDS")

    all_records = []

    for page in range(1, MAX_ODDS_PAGES + 1):

        if request_count >= MAX_TOTAL_REQUESTS:
            print("Maximum request limit reached.")
            break

        if (
            api_remaining is not None
            and api_remaining <= MIN_REMAINING_QUOTA
        ):
            print(
                "Quota reserve reached:",
                api_remaining
            )
            break

        print(f"Requesting odds page {page}...")

        try:

            payload = api_get(
                "odds",
                {
                    "date": TARGET_DATE,
                    "timezone": TIMEZONE,
                    "page": page
                }
            )

            records = payload.get("response", [])

            print(
                f"Page {page}: {len(records)} records"
            )

            all_records.extend(records)

            paging = payload.get("paging") or {}
            total_pages = paging.get("total", 1)

            if page >= total_pages:
                break

        except Exception as error:

            message = f"Odds page {page}: {error}"

            print("ODDS ERROR:", message)
            api_errors.append(message)

            break

    print("Total raw odds:", len(all_records))

    return all_records


# ============================================================
# MATCH FIXTURES WITH ODDS
# ============================================================

def build_linked_data(fixture_map, raw_odds):

    print("\n[3] MATCHING FIXTURES AND ODDS")

    linked = {}
    audit = []

    missing_fixture_id = 0
    missing_fixture_match = 0
    missing_team_names = 0
    bookmaker_records = 0

    for item in raw_odds:

        fixture = item.get("fixture") or {}
        fixture_id = fixture.get("id")

        if not fixture_id:

            missing_fixture_id += 1
            continue

        key = str(fixture_id)
        fixture_info = fixture_map.get(key)

        if not fixture_info:

            missing_fixture_match += 1

            audit.append({
                "fixture_id": fixture_id,
                "matched": False,
                "reason": "FIXTURE_NOT_FOUND"
            })

            continue

        home = fixture_info.get("home")
        away = fixture_info.get("away")

        if not home or not away:

            missing_team_names += 1
            continue

        bookmakers = item.get("bookmakers") or []

        if key not in linked:

            linked[key] = {
                "fixture_id": fixture_id,

                "fixture_info": {
                    "fixture_id": fixture_id,
                    "date": fixture_info.get("date"),
                    "timestamp": fixture_info.get("timestamp"),

                    # Tương thích với V3.3.1
                    "status": {
                        "short": fixture_info.get(
                            "status_short", "NS"
                        )
                    },

                    "home": home,
                    "away": away,
                    "league": fixture_info.get("league"),
                    "country": fixture_info.get("country"),
                    "league_id": fixture_info.get("league_id"),
                    "season": fixture_info.get("season")
                },

                "bookmakers": []
            }

        existing_names = {
            str(b.get("id") or b.get("name"))
            for b in linked[key]["bookmakers"]
        }

        for bookmaker in bookmakers:

            bookmaker_key = str(
                bookmaker.get("id")
                or bookmaker.get("name")
            )

            if bookmaker_key not in existing_names:

                linked[key]["bookmakers"].append(bookmaker)
                existing_names.add(bookmaker_key)

                bookmaker_records += 1

        audit.append({
            "fixture_id": fixture_id,
            "matched": True,
            "home": home,
            "away": away,
            "bookmakers": len(bookmakers)
        })

    print("Matched fixtures:", len(linked))
    print("Bookmaker records:", bookmaker_records)

    return (
        list(linked.values()),
        audit,
        missing_fixture_id,
        missing_fixture_match,
        missing_team_names,
        bookmaker_records
    )


# ============================================================
# SAVE OUTPUT
# ============================================================

def save_output(
    fixture_map,
    raw_odds,
    linked,
    audit,
    missing_fixture_id,
    missing_fixture_match,
    missing_team_names,
    bookmaker_records
):

    output = {

        "engine": "AI FOOTBALL PREDICTION V3.4.4.2",
        "source": "API-FOOTBALL",
        "query_method": "FIXTURES_AND_ODDS_BY_DATE_FIXTURE_ID",

        "date": TARGET_DATE,
        "timezone": TIMEZONE,

        "started_at": RUN_TIME.isoformat(),
        "finished_at": datetime.now(VN_TZ).isoformat(),

        "configuration": {
            "target_date": TARGET_DATE,
            "max_odds_pages": MAX_ODDS_PAGES,
            "max_total_requests": MAX_TOTAL_REQUESTS,
            "minimum_remaining_quota": MIN_REMAINING_QUOTA
        },

        "summary": {

            "fixtures_received": len(fixture_map),
            "raw_odds_records": len(raw_odds),
            "linked_upcoming_matches": len(linked),
            "bookmaker_records": bookmaker_records,

            "api_requests_used": request_count,
            "api_remaining": api_remaining,
            "api_limit": api_limit,

            "missing_fixture_id": missing_fixture_id,
            "fixture_not_found": missing_fixture_match,
            "missing_team_names": missing_team_names,

            "api_errors": len(api_errors)
        },

        "api_error_details": api_errors,
        "raw_odds_audit": audit,
        "odds": linked
    }

    temporary_file = OUTPUT_DIR / "latest.tmp"

    with open(
        temporary_file,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            output,
            file,
            ensure_ascii=False,
            indent=2
        )

    temporary_file.replace(LATEST_FILE)

    archive_name = (
        "v3442_"
        + datetime.now(VN_TZ).strftime("%Y%m%d_%H%M%S")
        + ".json"
    )

    archive_file = OUTPUT_DIR / archive_name

    with open(
        archive_file,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            output,
            file,
            ensure_ascii=False,
            indent=2
        )

    print("\n[4] SAVED DATA")
    print("Latest:", LATEST_FILE)
    print("Archive:", archive_file)

    return output


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 65)
    print("AI FOOTBALL PREDICTION V3.4.4.2")
    print("ADAPTIVE QUOTA + FIXTURE/ODDS MATCHING")
    print("=" * 65)

    if not API_KEY:

        raise RuntimeError(
            "Missing API_FOOTBALL_KEY GitHub Secret."
        )

    fixture_map = {}
    raw_odds = []

    try:

        fixture_map = collect_fixtures()

    except Exception as error:

        message = f"Fixtures: {error}"

        print("FIXTURES ERROR:", message)
        api_errors.append(message)

    if fixture_map:

        try:

            raw_odds = collect_odds()

        except Exception as error:

            message = f"Odds: {error}"

            print("ODDS ERROR:", message)
            api_errors.append(message)

    result = build_linked_data(
        fixture_map,
        raw_odds
    )

    (
        linked,
        audit,
        missing_fixture_id,
        missing_fixture_match,
        missing_team_names,
        bookmaker_records
    ) = result

    output = save_output(
        fixture_map,
        raw_odds,
        linked,
        audit,
        missing_fixture_id,
        missing_fixture_match,
        missing_team_names,
        bookmaker_records
    )

    print("\n===== V3.4.4.2 SUMMARY =====")

    for key, value in output["summary"].items():

        print(f"{key}: {value}")

    print("\nV3.4.4.2 COMPLETED")


if __name__ == "__main__":
    main()
