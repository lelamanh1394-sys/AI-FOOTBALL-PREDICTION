
import os
import json
import time
import requests

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from collections import defaultdict

# ==========================================================
# AI FOOTBALL PREDICTION V3.2
# GLOBAL LEAGUE ODDS SCANNER
#
# Purpose:
# - Collect worldwide daily fixtures
# - Scan odds by league and season
# - Rotate leagues across scheduled runs
# - Protect API daily quota
# - Preserve partial data
#
# IMPORTANT:
# This version collects data only.
# It does NOT generate betting predictions.
# ==========================================================

API_KEY = os.getenv("API_FOOTBALL_KEY")
BASE_URL = "https://v3.football.api-sports.io"

TIMEZONE = "Asia/Ho_Chi_Minh"
LOCAL_TZ = ZoneInfo(TIMEZONE)

# ----------------------------------------------------------
# API SAFETY SETTINGS
# ----------------------------------------------------------

MAX_LEAGUES_PER_RUN = 8

# Free plan: never request page 4.
# V3.2 deliberately collects page 1 per selected league.
MAX_ODDS_PAGES_PER_LEAGUE = 1

MIN_DAILY_REMAINING = 20

REQUEST_DELAY_SECONDS = 10.5
REQUEST_TIMEOUT_SECONDS = 30

DATA_DIR = Path("data/online")

SESSION = requests.Session()
SESSION.headers.update({
    "x-apisports-key": API_KEY or ""
})

LAST_DAILY_REMAINING = None
REQUESTS_THIS_RUN = 0


class ApiRequestError(Exception):
    pass


# ==========================================================
# TIME AND API
# ==========================================================

def now_local():
    return datetime.now(LOCAL_TZ)


def api_get(endpoint, params):
    global LAST_DAILY_REMAINING
    global REQUESTS_THIS_RUN

    if (
        LAST_DAILY_REMAINING is not None
        and LAST_DAILY_REMAINING <= MIN_DAILY_REMAINING
    ):
        raise ApiRequestError(
            "Daily API reserve protection activated"
        )

    url = f"{BASE_URL}/{endpoint}"

    try:
        response = SESSION.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS
        )

        REQUESTS_THIS_RUN += 1

        print(
            f"API {endpoint}: HTTP "
            f"{response.status_code}"
        )

        remaining = response.headers.get(
            "x-ratelimit-requests-remaining"
        )

        limit = response.headers.get(
            "x-ratelimit-requests-limit"
        )

        if remaining is not None:
            LAST_DAILY_REMAINING = int(remaining)

            print(
                "Daily quota remaining: "
                f"{LAST_DAILY_REMAINING}/{limit}"
            )

        if response.status_code == 429:
            raise ApiRequestError(
                "HTTP 429: API rate limit reached"
            )

        response.raise_for_status()

        payload = response.json()

        errors = payload.get("errors")

        if errors:
            raise ApiRequestError(
                f"API returned errors: {errors}"
            )

        return payload

    except requests.RequestException as exc:
        raise ApiRequestError(
            f"Request failed: {exc}"
        ) from exc


# ==========================================================
# FIXTURE COLLECTION
# ==========================================================

def collect_fixtures(date_string):

    print("\n[1] COLLECTING WORLDWIDE FIXTURES")

    payload = api_get(
        "fixtures",
        {
            "date": date_string,
            "timezone": TIMEZONE
        }
    )

    fixtures = payload.get("response", [])

    print(
        f"Fixtures collected: {len(fixtures)}"
    )

    return fixtures, payload.get("paging", {})


# ==========================================================
# LEAGUE GROUPING
# ==========================================================

def group_fixtures_by_league(fixtures):

    groups = {}

    for item in fixtures:

        league = item.get("league", {})

        league_id = league.get("id")
        season = league.get("season")

        if league_id is None or season is None:
            continue

        key = f"{league_id}_{season}"

        if key not in groups:
            groups[key] = {
                "league_id": league_id,
                "season": season,
                "name": league.get("name"),
                "country": league.get("country"),
                "fixture_count": 0
            }

        groups[key]["fixture_count"] += 1

    return list(groups.values())


# ==========================================================
# ROTATING LEAGUE SELECTION
# ==========================================================

def select_leagues_for_scan(leagues, current_time):

    if not leagues:
        return [], {
            "rotation_slot": None,
            "available_leagues": 0,
            "selected_leagues": 0
        }

    # Sort larger leagues first.
    leagues = sorted(
        leagues,
        key=lambda x: (
            -x["fixture_count"],
            str(x["league_id"])
        )
    )

    # Scheduled runs in Vietnam:
    # approximately 03:00, 09:00, 15:00, 21:00.
    rotation_slot = current_time.hour // 6

    total = len(leagues)

    if total <= MAX_LEAGUES_PER_RUN:
        selected = leagues
    else:
        start = (
            rotation_slot * MAX_LEAGUES_PER_RUN
        ) % total

        selected = []

        for offset in range(MAX_LEAGUES_PER_RUN):
            index = (start + offset) % total
            selected.append(leagues[index])

    metadata = {
        "rotation_slot": rotation_slot,
        "available_leagues": total,
        "selected_leagues": len(selected)
    }

    return selected, metadata


# ==========================================================
# ODDS COLLECTION BY LEAGUE
# ==========================================================

def collect_odds_by_league(
    date_string,
    selected_leagues
):

    print("\n[2] COLLECTING ODDS BY LEAGUE")

    all_odds = []
    league_results = []

    for number, league in enumerate(
        selected_leagues,
        start=1
    ):

        league_id = league["league_id"]
        season = league["season"]

        league_name = league.get("name")

        print("\n" + "-" * 50)

        print(
            f"League {number}/"
            f"{len(selected_leagues)}"
        )

        print(
            f"{league_name} "
            f"| ID: {league_id} "
            f"| Season: {season}"
        )

        result = {
            "league_id": league_id,
            "season": season,
            "league_name": league_name,
            "country": league.get("country"),
            "fixture_count": league.get("fixture_count"),
            "pages_requested": 0,
            "records_received": 0,
            "provider_total_pages": None,
            "status": "not_scanned",
            "error": None
        }

        if number > 1:
            time.sleep(REQUEST_DELAY_SECONDS)

        try:

            payload = api_get(
                "odds",
                {
                    "date": date_string,
                    "league": league_id,
                    "season": season,
                    "timezone": TIMEZONE,
                    "page": 1
                }
            )

            rows = payload.get("response", [])

            paging = payload.get("paging", {})

            total_pages = paging.get("total")

            result["pages_requested"] = 1
            result["records_received"] = len(rows)
            result["provider_total_pages"] = total_pages
            result["status"] = "success"

            all_odds.extend(rows)

            if (
                total_pages is not None
                and int(total_pages) > MAX_ODDS_PAGES_PER_LEAGUE
            ):
                result["status"] = (
                    "partial_more_pages_available"
                )

            print(
                f"Odds records received: {len(rows)}"
            )

            print(
                f"Provider pages: {total_pages}"
            )

        except (ApiRequestError, ValueError) as exc:

            result["status"] = "error"
            result["error"] = str(exc)

            print(
                f"League scan stopped: {exc}"
            )

            if (
                "reserve protection" in str(exc).lower()
                or "429" in str(exc)
                or "rate limit" in str(exc).lower()
            ):
                league_results.append(result)

                # Stop the remaining league scans.
                for remaining_league in selected_leagues[number:]:

                    league_results.append({
                        "league_id": remaining_league["league_id"],
                        "season": remaining_league["season"],
                        "league_name": remaining_league.get("name"),
                        "country": remaining_league.get("country"),
                        "fixture_count": remaining_league.get(
                            "fixture_count"
                        ),
                        "pages_requested": 0,
                        "records_received": 0,
                        "provider_total_pages": None,
                        "status": "skipped_quota_protection",
                        "error": None
                    })

                break

        league_results.append(result)

    return all_odds, league_results


# ==========================================================
# FIXTURE INDEX
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
            "venue": fixture.get("venue", {}),
            "league": {
                "id": league.get("id"),
                "name": league.get("name"),
                "country": league.get("country"),
                "season": league.get("season"),
                "round": league.get("round")
            },
            "home": teams.get("home", {}),
            "away": teams.get("away", {})
        }

    return index


# ==========================================================
# ODDS DEDUPLICATION
# ==========================================================

def deduplicate_odds(odds_rows):

    merged = {}

    for row in odds_rows:

        fixture = row.get("fixture", {})
        fixture_id = fixture.get("id")

        if fixture_id is None:
            continue

        key = str(fixture_id)

        if key not in merged:
            merged[key] = {
                "fixture_id": fixture_id,
                "bookmakers": []
            }

        existing = {
            str(book.get("id", book.get("name", "")))
            for book in merged[key]["bookmakers"]
        }

        for bookmaker in row.get("bookmakers", []):

            book_key = str(
                bookmaker.get(
                    "id",
                    bookmaker.get("name", "")
                )
            )

            if book_key not in existing:

                merged[key]["bookmakers"].append(
                    bookmaker
                )

                existing.add(book_key)

    return list(merged.values())


# ==========================================================
# LINK ODDS TO FIXTURES
# ==========================================================

def link_odds_to_fixtures(
    odds_rows,
    fixture_index
):

    linked = []
    unmatched = 0

    for row in odds_rows:

        fixture_id = row.get("fixture_id")

        fixture_info = fixture_index.get(
            str(fixture_id)
        )

        if fixture_info is None:
            unmatched += 1

        linked.append({
            "fixture_id": fixture_id,
            "fixture_info": fixture_info,
            "bookmakers": row.get("bookmakers", [])
        })

    return linked, unmatched


# ==========================================================
# SAVE DATA
# ==========================================================

def save_json(path, data):

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temporary_path = path.with_suffix(
        path.suffix + ".tmp"
    )

    with temporary_path.open(
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2
        )

    temporary_path.replace(path)


# ==========================================================
# MAIN ENGINE
# ==========================================================

def main():

    global REQUESTS_THIS_RUN

    started = now_local()

    date_string = started.strftime("%Y-%m-%d")

    timestamp = started.strftime(
        "%Y%m%d_%H%M%S"
    )

    print("=" * 65)
    print("AI FOOTBALL PREDICTION V3.2")
    print("GLOBAL LEAGUE ODDS SCANNER")
    print(f"Vietnam date: {date_string}")
    print(f"Started: {started.isoformat()}")
    print("=" * 65)

    # ------------------------------------------------------
    # 1. FIXTURES
    # ------------------------------------------------------

    try:

        fixtures, fixture_paging = collect_fixtures(
            date_string
        )

    except ApiRequestError as exc:

        print(f"CRITICAL FIXTURE ERROR: {exc}")
        raise SystemExit(1)

    # ------------------------------------------------------
    # 2. GROUP LEAGUES
    # ------------------------------------------------------

    all_leagues = group_fixtures_by_league(
        fixtures
    )

    selected_leagues, rotation_info = (
        select_leagues_for_scan(
            all_leagues,
            started
        )
    )

    print("\n===== LEAGUE ROTATION =====")

    print(
        json.dumps(
            rotation_info,
            ensure_ascii=False,
            indent=2
        )
    )

    # ------------------------------------------------------
    # 3. COLLECT ODDS
    # ------------------------------------------------------

    raw_odds = []
    league_results = []

    if selected_leagues:

        raw_odds, league_results = (
            collect_odds_by_league(
                date_string,
                selected_leagues
            )
        )

    else:

        print(
            "No eligible league/season groups found."
        )

    # ------------------------------------------------------
    # 4. LINK DATA
    # ------------------------------------------------------

    unique_odds = deduplicate_odds(
        raw_odds
    )

    fixture_index = build_fixture_index(
        fixtures
    )

    linked_odds, unmatched = (
        link_odds_to_fixtures(
            unique_odds,
            fixture_index
        )
    )

    matched_count = len(linked_odds) - unmatched

    coverage = (
        round(
            matched_count / len(fixtures) * 100,
            2
        )
        if fixtures else 0
    )

    # ------------------------------------------------------
    # 5. SUMMARY
    # ------------------------------------------------------

    successful_leagues = sum(
        1
        for item in league_results
        if item["status"] in (
            "success",
            "partial_more_pages_available"
        )
    )

    failed_leagues = sum(
        1
        for item in league_results
        if item["status"] == "error"
    )

    skipped_leagues = sum(
        1
        for item in league_results
        if item["status"] == "skipped_quota_protection"
    )

    finished = now_local()

    summary_data = {
        "fixtures_found": len(fixtures),
        "available_leagues": len(all_leagues),
        "selected_leagues": len(selected_leagues),
        "successfully_scanned_leagues": successful_leagues,
        "failed_leagues": failed_leagues,
        "skipped_leagues": skipped_leagues,
        "odds_raw_records": len(raw_odds),
        "odds_unique_fixtures": len(unique_odds),
        "odds_linked_to_fixtures": matched_count,
        "odds_without_fixture_match": unmatched,
        "odds_coverage_percent": coverage
    }

    # ------------------------------------------------------
    # 6. FULL RESULT
    # ------------------------------------------------------

    result = {
        "engine": "AI Football Prediction V3.2",
        "mode": "global_league_odds_collection",
        "prediction_status": (
            "NOT_ENABLED: independent probability model "
            "and walk-forward validation are required."
        ),
        "date": date_string,
        "timezone": TIMEZONE,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),

        "configuration": {
            "max_leagues_per_run": MAX_LEAGUES_PER_RUN,
            "max_odds_pages_per_league":
                MAX_ODDS_PAGES_PER_LEAGUE,
            "minimum_daily_quota_reserve":
                MIN_DAILY_REMAINING,
            "request_delay_seconds":
                REQUEST_DELAY_SECONDS
        },

        "rotation": rotation_info,

        "summary": summary_data,

        "quota": {
            "requests_this_run": REQUESTS_THIS_RUN,
            "daily_remaining_after_last_request":
                LAST_DAILY_REMAINING,
            "minimum_daily_reserve":
                MIN_DAILY_REMAINING
        },

        "fixture_paging": fixture_paging,

        "all_leagues": all_leagues,

        "selected_leagues": selected_leagues,

        "league_scan_results": league_results,

        "fixtures": fixtures,

        "odds": linked_odds
    }

    # ------------------------------------------------------
    # 7. SAVE FILES
    # ------------------------------------------------------

    day_dir = DATA_DIR / date_string

    snapshot_path = (
        day_dir / f"scan_{timestamp}.json"
    )

    save_json(
        snapshot_path,
        result
    )

    save_json(
        day_dir / "latest.json",
        result
    )

    summary = {
        "engine": result["engine"],
        "date": date_string,
        "finished_at": finished.isoformat(),
        **summary_data,
        "rotation": rotation_info,
        "quota": result["quota"],
        "prediction_status":
            result["prediction_status"],
        "snapshot": str(snapshot_path)
    }

    save_json(
        day_dir / "summary.json",
        summary
    )

    # ------------------------------------------------------
    # 8. CONSOLE REPORT
    # ------------------------------------------------------

    print("\n" + "=" * 65)
    print("V3.2 FINAL SCAN SUMMARY")
    print("=" * 65)

    print(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2
        )
    )

    print("\nLEAGUE SCAN DETAILS")

    for item in league_results:

        print(
            f'{item["league_name"]} | '
            f'{item["status"]} | '
            f'odds: {item["records_received"]}'
        )

    print("\nV3.2 SCAN COMPLETED")


if __name__ == "__main__":

    if not API_KEY:
        raise SystemExit(
            "ERROR: Missing API_FOOTBALL_KEY secret"
        )

    main()
