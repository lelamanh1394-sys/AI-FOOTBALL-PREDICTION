
import os
import json
import time
import requests

from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# ==========================================================
# AI FOOTBALL PREDICTION V3.4
# GLOBAL LEAGUE ODDS SCANNER
# ==========================================================

API_KEY = os.getenv("API_FOOTBALL_KEY")
BASE_URL = "https://v3.football.api-sports.io"

TIMEZONE = "Asia/Ho_Chi_Minh"
TZ = ZoneInfo(TIMEZONE)

OUTPUT_DIR = Path("data/public_odds")

# SAFETY SETTINGS
MAX_LEAGUES_PER_RUN = 8
MAX_ODDS_PAGES_PER_LEAGUE = 1
MIN_DAILY_REMAINING = 20

REQUEST_DELAY = 1.2
TIMEOUT = 30

session = requests.Session()
session.headers.update({
    "x-apisports-key": API_KEY or ""
})

LAST_REMAINING = None
REQUESTS_USED = 0


# ==========================================================
# TIME
# ==========================================================

def now_local():
    return datetime.now(TZ)


# ==========================================================
# API REQUEST
# ==========================================================

def api_get(endpoint, params):

    global LAST_REMAINING
    global REQUESTS_USED

    if (
        LAST_REMAINING is not None
        and LAST_REMAINING <= MIN_DAILY_REMAINING
    ):
        raise RuntimeError(
            "API reserve protection activated"
        )

    response = session.get(
        f"{BASE_URL}/{endpoint}",
        params=params,
        timeout=TIMEOUT
    )

    REQUESTS_USED += 1

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

        LAST_REMAINING = int(remaining)

        print(
            f"API quota remaining: "
            f"{LAST_REMAINING}/{limit}"
        )

    if response.status_code == 429:
        raise RuntimeError(
            "API rate limit reached"
        )

    if response.status_code in (401, 403):
        raise RuntimeError(
            f"API authorization failed: "
            f"HTTP {response.status_code}"
        )

    response.raise_for_status()

    payload = response.json()

    if payload.get("errors"):
        raise RuntimeError(
            f"API errors: {payload['errors']}"
        )

    return payload


# ==========================================================
# 1. COLLECT FIXTURES
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
        "Fixtures received:",
        len(fixtures)
    )

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
# 3. GROUP FIXTURES BY LEAGUE
# ==========================================================

def group_leagues(fixtures):

    groups = {}

    for item in fixtures:

        league = item.get("league", {})

        league_id = league.get("id")

        if league_id is None:
            continue

        key = str(league_id)

        if key not in groups:

            groups[key] = {
                "league_id": league_id,
                "name": league.get("name"),
                "country": league.get("country"),
                "fixture_count": 0
            }

        groups[key]["fixture_count"] += 1

    return list(groups.values())


# ==========================================================
# 4. ROTATING LEAGUE SELECTION
# ==========================================================

def select_leagues(leagues, current_time):

    if not leagues:
        return [], {}

    leagues = sorted(
        leagues,
        key=lambda x: (
            -x["fixture_count"],
            str(x["league_id"])
        )
    )

    total = len(leagues)

    # Two different rotation slots:
    # Morning and evening Vietnam time.
    slot = 0 if current_time.hour < 15 else 1

    start = (
        current_time.date().toordinal() * 2
        + slot
    ) * MAX_LEAGUES_PER_RUN

    start %= total

    selected = []

    count = min(
        MAX_LEAGUES_PER_RUN,
        total
    )

    for offset in range(count):

        index = (start + offset) % total

        selected.append(leagues[index])

    metadata = {
        "available_leagues": total,
        "selected_leagues": len(selected),
        "rotation_slot": slot,
        "rotation_start": start
    }

    return selected, metadata


# ==========================================================
# 5. COLLECT ODDS BY LEAGUE
# ==========================================================

def collect_odds_by_league(
    date_string,
    selected_leagues
):

    print("\n[2] COLLECTING ODDS BY LEAGUE")

    all_rows = []
    scan_results = []

    for number, league in enumerate(
        selected_leagues,
        start=1
    ):

        league_id = league["league_id"]

        print("\n" + "-" * 55)

        print(
            f"League {number}/"
            f"{len(selected_leagues)}"
        )

        print(
            league["name"],
            "|",
            league["country"],
            "| ID:",
            league_id
        )

        result = {
            "league_id": league_id,
            "league_name": league["name"],
            "country": league["country"],
            "fixture_count": league["fixture_count"],
            "records_received": 0,
            "provider_total_pages": None,
            "status": "pending",
            "error": None
        }

        if (
            LAST_REMAINING is not None
            and LAST_REMAINING <= MIN_DAILY_REMAINING
        ):

            result["status"] = "quota_protection"

            scan_results.append(result)

            print("Stopping: API reserve protection.")

            break

        try:

            # IMPORTANT:
            # Use date + league.
            # Do NOT send season parameter.

            payload = api_get(
                "odds",
                {
                    "date": date_string,
                    "league": league_id,
                    "page": 1
                }
            )

            rows = payload.get("response", [])

            paging = payload.get("paging", {})

            total_pages = paging.get("total", 1)

            result["records_received"] = len(rows)

            result["provider_total_pages"] = total_pages

            result["status"] = "success"

            all_rows.extend(rows)

            print(
                "Odds records:",
                len(rows)
            )

            print(
                "Provider pages:",
                total_pages
            )

            if int(total_pages) > 1:

                result["status"] = "partial"

                print(
                    "More pages exist. "
                    "Skipping them to protect API quota."
                )

        except Exception as exc:

            result["status"] = "error"

            result["error"] = str(exc)

            print("League error:", exc)

            if any(
                word in str(exc).lower()
                for word in (
                    "quota",
                    "rate limit",
                    "429",
                    "reserve protection"
                )
            ):

                scan_results.append(result)

                break

        scan_results.append(result)

        if number < len(selected_leagues):
            time.sleep(REQUEST_DELAY)

    return all_rows, scan_results


# ==========================================================
# 6. MERGE ODDS AND MATCH FIXTURES
# ==========================================================

def merge_odds(
    raw_rows,
    fixture_index
):

    print("\n[3] LINKING ODDS TO FIXTURES")

    merged = {}

    unmatched = 0
    invalid = 0

    now_timestamp = int(time.time())

    audit = []

    for row in raw_rows:

        fixture = row.get("fixture", {})

        fixture_id = fixture.get("id")

        if fixture_id is None:

            unmatched += 1

            continue

        key = str(fixture_id)

        fixture_info = fixture_index.get(key)

        if fixture_info is None:

            unmatched += 1

            audit.append({
                "fixture_id": fixture_id,
                "linked": False,
                "reason": "NOT_IN_FIXTURE_DATE"
            })

            continue

        status = fixture_info.get(
            "status", {}
        ).get("short")

        timestamp = fixture_info.get("timestamp")

        if (
            status not in ("NS", "TBD")
            or not timestamp
            or int(timestamp) <= now_timestamp
        ):

            invalid += 1

            audit.append({
                "fixture_id": fixture_id,
                "linked": False,
                "reason": "NOT_UPCOMING"
            })

            continue

        if key not in merged:

            merged[key] = {
                "fixture_id": fixture_id,
                "fixture_info": fixture_info,
                "bookmakers": []
            }

        existing = {
            str(
                bookmaker.get(
                    "id",
                    bookmaker.get("name", "")
                )
            )
            for bookmaker in merged[key]["bookmakers"]
        }

        for bookmaker in row.get(
            "bookmakers", []
        ):

            bookmaker_key = str(
                bookmaker.get(
                    "id",
                    bookmaker.get("name", "")
                )
            )

            if bookmaker_key not in existing:

                merged[key]["bookmakers"].append(
                    bookmaker
                )

                existing.add(bookmaker_key)

        audit.append({
            "fixture_id": fixture_id,
            "linked": True,
            "reason": "OK",
            "bookmaker_count": len(
                row.get("bookmakers", [])
            )
        })

    linked = list(merged.values())

    print("Raw odds records:", len(raw_rows))

    print("Linked matches:", len(linked))

    print("Unmatched records:", unmatched)

    print("Invalid or started matches:", invalid)

    return linked, audit, unmatched, invalid


# ==========================================================
# 7. SAVE DATA
# ==========================================================

def save_data(
    date_string,
    started_at,
    fixtures,
    leagues,
    selected,
    rotation,
    raw_rows,
    linked,
    scan_results,
    audit,
    unmatched,
    invalid
):

    print("\n[4] SAVING V3.4 DATA")

    date_dir = OUTPUT_DIR / date_string

    date_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    bookmaker_count = sum(
        len(item.get("bookmakers", []))
        for item in linked
    )

    finished_at = now_local().isoformat()

    data = {

        "engine": "AI FOOTBALL PREDICTION V3.4",

        "source": "API-FOOTBALL",

        "query_method": "DATE_AND_LEAGUE_WITHOUT_SEASON",

        "date": date_string,

        "timezone": TIMEZONE,

        "started_at": started_at,

        "finished_at": finished_at,

        "configuration": {

            "max_leagues_per_run": MAX_LEAGUES_PER_RUN,

            "max_odds_pages_per_league": (
                MAX_ODDS_PAGES_PER_LEAGUE
            ),

            "season_parameter_used": False,

            "minimum_remaining_quota": (
                MIN_DAILY_REMAINING
            )
        },

        "rotation": rotation,

        "summary": {

            "fixtures_received": len(fixtures),

            "available_leagues": len(leagues),

            "selected_leagues": len(selected),

            "league_requests_successful": sum(
                1 for item in scan_results
                if item["status"] in (
                    "success",
                    "partial"
                )
            ),

            "raw_odds_records": len(raw_rows),

            "linked_upcoming_matches": len(linked),

            "bookmaker_records": bookmaker_count,

            "unmatched_records": unmatched,

            "invalid_records": invalid,

            "api_requests_used": REQUESTS_USED,

            "api_remaining": LAST_REMAINING
        },

        "league_scan_results": scan_results,

        "raw_odds_audit": audit,

        "odds": linked
    }

    latest_file = date_dir / "latest.json"

    timestamp = now_local().strftime(
        "%Y%m%d_%H%M%S"
    )

    archive_file = date_dir / (
        f"v34_{timestamp}.json"
    )

    for path in (latest_file, archive_file):

        temp = path.with_suffix(".tmp")

        with temp.open(
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2
            )

        temp.replace(path)

    print("Saved:", latest_file)

    print("Archive:", archive_file)

    return data


# ==========================================================
# 8. MAIN
# ==========================================================

def main():

    print("=" * 65)

    print("AI FOOTBALL PREDICTION V3.4")

    print("GLOBAL LEAGUE ODDS SCANNER")

    print("=" * 65)

    if not API_KEY:

        raise RuntimeError(
            "Missing API_FOOTBALL_KEY secret."
        )

    started_at = now_local().isoformat()

    date_string = now_local().strftime(
        "%Y-%m-%d"
    )

    print("Vietnam date:", date_string)

    print("Maximum leagues:", MAX_LEAGUES_PER_RUN)

    print("Season parameter: DISABLED")

    # 1. Fixtures
    fixtures = collect_fixtures(date_string)

    fixture_index = build_fixture_index(
        fixtures
    )

    # 2. Group leagues
    leagues = group_leagues(fixtures)

    print(
        "\nAvailable leagues:",
        len(leagues)
    )

    # 3. Rotate leagues
    selected, rotation = select_leagues(
        leagues,
        now_local()
    )

    print(
        "Selected leagues:",
        len(selected)
    )

    # 4. Collect odds by league
    raw_rows, scan_results = (
        collect_odds_by_league(
            date_string,
            selected
        )
    )

    # 5. Link odds
    linked, audit, unmatched, invalid = (
        merge_odds(
            raw_rows,
            fixture_index
        )
    )

    # 6. Save
    data = save_data(
        date_string,
        started_at,
        fixtures,
        leagues,
        selected,
        rotation,
        raw_rows,
        linked,
        scan_results,
        audit,
        unmatched,
        invalid
    )

    # 7. Summary
    print("\n" + "=" * 65)

    print("V3.4 COLLECTION SUMMARY")

    print("=" * 65)

    for key, value in data["summary"].items():

        print(f"{key}: {value}")

    print("\n===== LEAGUE RESULTS =====")

    for item in scan_results:

        print(
            item["league_name"],
            "|",
            item["status"],
            "| Records:",
            item["records_received"]
        )

    print("\nV3.4 COMPLETED")


if __name__ == "__main__":

    try:

        main()

    except Exception as error:

        print("\nV3.4 FAILED:")

        print(error)

        raise
