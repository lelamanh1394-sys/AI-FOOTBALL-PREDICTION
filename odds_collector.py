
import os
import json
import time
import requests

from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

# ==========================================================
# AI FOOTBALL PREDICTION V3.4.1
# FIXTURE MAPPING + DIRECT ODDS SCANNER
# ==========================================================

API_KEY = os.getenv("API_FOOTBALL_KEY")
BASE_URL = "https://v3.football.api-sports.io"

TIMEZONE = "Asia/Ho_Chi_Minh"
TZ = ZoneInfo(TIMEZONE)

OUTPUT_DIR = Path("data/public_odds")

# SAFETY LIMITS
MAX_MAPPING_PAGES = 3
MAX_ODDS_FIXTURES_PER_RUN = 6
MIN_DAILY_REMAINING = 20

REQUEST_DELAY = 1.2
TIMEOUT = 30

session = requests.Session()
session.headers.update({
    "x-apisports-key": API_KEY or ""
})

LAST_REMAINING = None
REQUESTS_USED = 0


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
        raise RuntimeError("API reserve protection activated")

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
        print(f"API quota remaining: {LAST_REMAINING}/{limit}")

    if response.status_code == 429:
        raise RuntimeError("API rate limit reached")

    if response.status_code in (401, 403):
        raise RuntimeError(
            f"API authorization failed: HTTP {response.status_code}"
        )

    response.raise_for_status()

    payload = response.json()

    if payload.get("errors"):
        raise RuntimeError(f"API errors: {payload['errors']}")

    return payload


def is_quota_error(error):
    message = str(error).lower()
    return any(word in message for word in (
        "quota",
        "rate limit",
        "429",
        "reserve protection"
    ))


# ==========================================================
# 1. FIXTURES
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


def build_fixture_index(fixtures):

    index = {}
    now_timestamp = int(time.time())

    for item in fixtures:

        fixture = item.get("fixture", {})
        teams = item.get("teams", {})
        league = item.get("league", {})

        fixture_id = fixture.get("id")

        if fixture_id is None:
            continue

        timestamp = fixture.get("timestamp")
        status = fixture.get("status", {}).get("short")

        index[str(fixture_id)] = {
            "fixture_id": fixture_id,
            "date": fixture.get("date"),
            "timestamp": timestamp,
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

    upcoming = {}

    for key, info in index.items():

        status = info.get("status", {}).get("short")
        timestamp = info.get("timestamp")

        if (
            status in ("NS", "TBD")
            and timestamp
            and int(timestamp) > now_timestamp
        ):
            upcoming[key] = info

    return index, upcoming


# ==========================================================
# 2. ODDS MAPPING
# ==========================================================

def collect_mapping():

    print("\n[2] COLLECTING ODDS MAPPING")

    mapping_rows = []
    page_results = []

    for page in range(1, MAX_MAPPING_PAGES + 1):

        if (
            LAST_REMAINING is not None
            and LAST_REMAINING <= MIN_DAILY_REMAINING
        ):
            print("Mapping stopped by quota protection.")
            break

        try:

            payload = api_get(
                "odds/mapping",
                {"page": page}
            )

            rows = payload.get("response", [])
            paging = payload.get("paging", {})

            total_pages = int(paging.get("total", 1))

            mapping_rows.extend(rows)

            page_results.append({
                "page": page,
                "records": len(rows),
                "total_pages": total_pages,
                "status": "success"
            })

            print(
                f"Mapping page {page}: "
                f"{len(rows)} records; "
                f"provider pages={total_pages}"
            )

            if page >= total_pages:
                break

            if page < MAX_MAPPING_PAGES:
                time.sleep(REQUEST_DELAY)

        except Exception as exc:

            page_results.append({
                "page": page,
                "records": 0,
                "status": "error",
                "error": str(exc)
            })

            print("Mapping error:", exc)

            if is_quota_error(exc):
                break

    return mapping_rows, page_results


def extract_mapping_fixture_id(row):

    fixture = row.get("fixture", {})

    if isinstance(fixture, dict):
        return fixture.get("id")

    return None


def match_mapping_to_fixtures(mapping_rows, upcoming):

    available_ids = set()

    for row in mapping_rows:

        fixture_id = extract_mapping_fixture_id(row)

        if fixture_id is None:
            continue

        key = str(fixture_id)

        if key in upcoming:
            available_ids.add(key)

    candidates = [
        upcoming[key]
        for key in available_ids
    ]

    candidates.sort(
        key=lambda item: int(item.get("timestamp") or 0)
    )

    print("\nMapping records:", len(mapping_rows))
    print("Today's upcoming fixtures:", len(upcoming))
    print("Fixtures with odds mapping:", len(candidates))

    return candidates


# ==========================================================
# 3. ROTATE FIXTURE SELECTION
# ==========================================================

def select_fixtures(candidates, current_time):

    if not candidates:
        return [], {
            "available_candidates": 0,
            "selected_fixtures": 0,
            "rotation_slot": None,
            "rotation_start": 0
        }

    total = len(candidates)

    slot = 0 if current_time.hour < 15 else 1

    start = (
        current_time.date().toordinal() * 2 + slot
    ) * MAX_ODDS_FIXTURES_PER_RUN

    start %= total

    count = min(
        MAX_ODDS_FIXTURES_PER_RUN,
        total
    )

    selected = []

    for offset in range(count):
        selected.append(
            candidates[(start + offset) % total]
        )

    metadata = {
        "available_candidates": total,
        "selected_fixtures": len(selected),
        "rotation_slot": slot,
        "rotation_start": start
    }

    return selected, metadata


# ==========================================================
# 4. FETCH ODDS BY FIXTURE ID
# ==========================================================

def collect_odds_by_fixture(selected):

    print("\n[3] COLLECTING ODDS BY FIXTURE ID")

    all_rows = []
    results = []

    for number, fixture_info in enumerate(selected, start=1):

        fixture_id = fixture_info["fixture_id"]

        home = fixture_info.get("home")
        away = fixture_info.get("away")

        result = {
            "fixture_id": fixture_id,
            "home": home,
            "away": away,
            "records_received": 0,
            "status": "pending",
            "error": None
        }

        print("\n" + "-" * 55)
        print(f"Fixture {number}/{len(selected)}")
        print(f"{home} - {away} | ID: {fixture_id}")

        if (
            LAST_REMAINING is not None
            and LAST_REMAINING <= MIN_DAILY_REMAINING
        ):
            result["status"] = "quota_protection"
            results.append(result)
            print("Stopping: quota reserve protection.")
            break

        try:

            # Direct fixture query.
            # No season parameter is sent.

            payload = api_get(
                "odds",
                {"fixture": fixture_id}
            )

            rows = payload.get("response", [])

            result["records_received"] = len(rows)
            result["status"] = (
                "success" if rows else "no_odds"
            )

            all_rows.extend(rows)

            print("Odds records:", len(rows))

        except Exception as exc:

            result["status"] = "error"
            result["error"] = str(exc)

            print("Fixture odds error:", exc)

            if is_quota_error(exc):
                results.append(result)
                break

        results.append(result)

        if number < len(selected):
            time.sleep(REQUEST_DELAY)

    return all_rows, results


# ==========================================================
# 5. MERGE AND AUDIT
# ==========================================================

def merge_odds(raw_rows, fixture_index, upcoming):

    merged = {}
    audit = []

    unmatched = 0
    invalid = 0

    for row in raw_rows:

        fixture_id = row.get("fixture", {}).get("id")

        if fixture_id is None:
            unmatched += 1
            audit.append({
                "fixture_id": None,
                "linked": False,
                "reason": "MISSING_FIXTURE_ID"
            })
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

        if key not in upcoming:
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
            str(bookmaker.get(
                "id",
                bookmaker.get("name", "")
            ))
            for bookmaker in merged[key]["bookmakers"]
        }

        for bookmaker in row.get("bookmakers", []):

            bookmaker_key = str(
                bookmaker.get(
                    "id",
                    bookmaker.get("name", "")
                )
            )

            if bookmaker_key not in existing:
                merged[key]["bookmakers"].append(bookmaker)
                existing.add(bookmaker_key)

        audit.append({
            "fixture_id": fixture_id,
            "linked": True,
            "reason": "OK",
            "bookmaker_count": len(row.get("bookmakers", []))
        })

    linked = list(merged.values())

    print("\nRaw odds records:", len(raw_rows))
    print("Linked upcoming matches:", len(linked))
    print("Unmatched records:", unmatched)
    print("Invalid records:", invalid)

    return linked, audit, unmatched, invalid


# ==========================================================
# 6. SAVE
# ==========================================================

def save_data(
    date_string,
    started_at,
    fixtures,
    upcoming,
    mapping_rows,
    mapping_results,
    selected,
    rotation,
    raw_rows,
    linked,
    fixture_results,
    audit,
    unmatched,
    invalid
):

    print("\n[4] SAVING V3.4.1 DATA")

    date_dir = OUTPUT_DIR / date_string
    date_dir.mkdir(parents=True, exist_ok=True)

    bookmaker_count = sum(
        len(item.get("bookmakers", []))
        for item in linked
    )

    finished_at = now_local().isoformat()

    data = {
        "engine": "AI FOOTBALL PREDICTION V3.4.1",
        "source": "API-FOOTBALL",
        "query_method": "ODDS_MAPPING_THEN_FIXTURE_ID",
        "date": date_string,
        "timezone": TIMEZONE,
        "started_at": started_at,
        "finished_at": finished_at,

        "configuration": {
            "max_mapping_pages": MAX_MAPPING_PAGES,
            "max_odds_fixtures_per_run": MAX_ODDS_FIXTURES_PER_RUN,
            "season_parameter_used": False,
            "minimum_remaining_quota": MIN_DAILY_REMAINING
        },

        "rotation": rotation,

        "summary": {
            "fixtures_received": len(fixtures),
            "upcoming_fixtures": len(upcoming),
            "mapping_records": len(mapping_rows),
            "mapping_pages_successful": sum(
                1 for item in mapping_results
                if item.get("status") == "success"
            ),
            "mapped_upcoming_fixtures": rotation.get(
                "available_candidates", 0
            ),
            "selected_fixtures": len(selected),
            "fixture_odds_requests_successful": sum(
                1 for item in fixture_results
                if item.get("status") == "success"
            ),
            "raw_odds_records": len(raw_rows),
            "linked_upcoming_matches": len(linked),
            "bookmaker_records": bookmaker_count,
            "unmatched_records": unmatched,
            "invalid_records": invalid,
            "api_requests_used": REQUESTS_USED,
            "api_remaining": LAST_REMAINING
        },

        "mapping_page_results": mapping_results,
        "fixture_odds_results": fixture_results,
        "raw_odds_audit": audit,
        "odds": linked
    }

    latest_file = date_dir / "latest.json"

    timestamp = now_local().strftime("%Y%m%d_%H%M%S")

    archive_file = date_dir / f"v341_{timestamp}.json"

    for path in (latest_file, archive_file):

        temp = path.with_suffix(".tmp")

        with temp.open("w", encoding="utf-8") as file:
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
# 7. MAIN
# ==========================================================

def main():

    print("=" * 65)
    print("AI FOOTBALL PREDICTION V3.4.1")
    print("FIXTURE MAPPING + DIRECT ODDS SCANNER")
    print("=" * 65)

    if not API_KEY:
        raise RuntimeError("Missing API_FOOTBALL_KEY secret.")

    started_at = now_local().isoformat()
    date_string = now_local().strftime("%Y-%m-%d")

    print("Vietnam date:", date_string)
    print("Mapping pages max:", MAX_MAPPING_PAGES)
    print("Odds fixture requests max:", MAX_ODDS_FIXTURES_PER_RUN)

    # 1. Fixtures
    fixtures = collect_fixtures(date_string)

    fixture_index, upcoming = build_fixture_index(fixtures)

    # 2. Mapping
    mapping_rows, mapping_results = collect_mapping()

    # 3. Match mapping IDs to today's fixtures
    candidates = match_mapping_to_fixtures(
        mapping_rows,
        upcoming
    )

    # 4. Rotate a limited number of fixtures
    selected, rotation = select_fixtures(
        candidates,
        now_local()
    )

    print("\nSelected fixtures:", len(selected))

    # 5. Direct odds lookup by fixture ID
    raw_rows, fixture_results = collect_odds_by_fixture(
        selected
    )

    # 6. Merge
    linked, audit, unmatched, invalid = merge_odds(
        raw_rows,
        fixture_index,
        upcoming
    )

    # 7. Save
    data = save_data(
        date_string,
        started_at,
        fixtures,
        upcoming,
        mapping_rows,
        mapping_results,
        selected,
        rotation,
        raw_rows,
        linked,
        fixture_results,
        audit,
        unmatched,
        invalid
    )

    print("\n" + "=" * 65)
    print("V3.4.1 COLLECTION SUMMARY")
    print("=" * 65)

    for key, value in data["summary"].items():
        print(f"{key}: {value}")

    print("\n===== FIXTURE ODDS RESULTS =====")

    for item in fixture_results:
        print(
            item["home"],
            "-",
            item["away"],
            "|",
            item["status"],
            "| Records:",
            item["records_received"]
        )

    print("\nV3.4.1 COMPLETED")


if __name__ == "__main__":

    try:
        main()

    except Exception as error:
        print("\nV3.4.1 FAILED:")
        print(error)
        raise
