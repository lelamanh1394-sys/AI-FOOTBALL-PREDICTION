
import os
import json
import time
import requests

from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# ==========================================================
# AI FOOTBALL PREDICTION V3.4.2
# 14-DAY FIXTURES + ROTATING MAPPING + DATE-BASED ODDS
# ==========================================================

API_KEY = os.getenv("API_FOOTBALL_KEY")
BASE_URL = "https://v3.football.api-sports.io"

TIMEZONE = "Asia/Ho_Chi_Minh"
TZ = ZoneInfo(TIMEZONE)

OUTPUT_DIR = Path("data/public_odds")
STATE_FILE = OUTPUT_DIR / "mapping_state.json"

# Strict API budget per run
MAX_TOTAL_REQUESTS = 5
MAX_MAPPING_REQUESTS = 1
MAX_ODDS_DATE_REQUESTS = 3
MIN_DAILY_REMAINING = 20

FIXTURE_LOOKAHEAD_DAYS = 14
REQUEST_DELAY = 1.2
TIMEOUT = 30

session = requests.Session()
session.headers.update({
    "x-apisports-key": API_KEY or ""
})

LAST_REMAINING = None
REQUESTS_USED = 0


# ==========================================================
# TIME AND FILE HELPERS
# ==========================================================

def now_local():
    return datetime.now(TZ)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)

    temp = path.with_suffix(path.suffix + ".tmp")

    with temp.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)

    temp.replace(path)


def load_json(path, default):
    if not path.exists():
        return default

    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        return default


# ==========================================================
# API REQUEST WITH STRICT QUOTA PROTECTION
# ==========================================================

def api_get(endpoint, params):

    global LAST_REMAINING
    global REQUESTS_USED

    if REQUESTS_USED >= MAX_TOTAL_REQUESTS:
        raise RuntimeError("V3.4.2 per-run request limit reached")

    if (
        LAST_REMAINING is not None
        and LAST_REMAINING <= MIN_DAILY_REMAINING
    ):
        raise RuntimeError("Daily API reserve protection activated")

    response = session.get(
        f"{BASE_URL}/{endpoint}",
        params=params,
        timeout=TIMEOUT
    )

    REQUESTS_USED += 1

    print(
        f"HTTP {response.status_code} | "
        f"{endpoint} | {params}"
    )

    remaining = response.headers.get(
        "x-ratelimit-requests-remaining"
    )

    limit = response.headers.get(
        "x-ratelimit-requests-limit"
    )

    if remaining is not None:
        LAST_REMAINING = int(remaining)
        print(f"API remaining: {LAST_REMAINING}/{limit}")

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


def quota_protected():
    if REQUESTS_USED >= MAX_TOTAL_REQUESTS:
        return True

    if (
        LAST_REMAINING is not None
        and LAST_REMAINING <= MIN_DAILY_REMAINING
    ):
        return True

    return False


# ==========================================================
# 1. COLLECT FIXTURES FOR THE NEXT 14 DAYS
# ==========================================================

def collect_fixtures():

    today = now_local().date()
    end_date = today + timedelta(days=FIXTURE_LOOKAHEAD_DAYS)

    print("\n[1] COLLECTING 14-DAY FIXTURE WINDOW")
    print("From:", today.isoformat())
    print("To:", end_date.isoformat())

    payload = api_get(
        "fixtures",
        {
            "from": today.isoformat(),
            "to": end_date.isoformat(),
            "timezone": TIMEZONE
        }
    )

    fixtures = payload.get("response", [])

    print("Fixtures received:", len(fixtures))

    return fixtures


def build_fixture_index(fixtures):

    index = {}
    upcoming = {}

    current_timestamp = int(time.time())

    for item in fixtures:

        fixture = item.get("fixture", {})
        teams = item.get("teams", {})
        league = item.get("league", {})

        fixture_id = fixture.get("id")

        if fixture_id is None:
            continue

        timestamp = fixture.get("timestamp")
        status = fixture.get("status", {}).get("short")

        info = {
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

        key = str(fixture_id)
        index[key] = info

        if (
            status in ("NS", "TBD")
            and timestamp
            and int(timestamp) > current_timestamp
        ):
            upcoming[key] = info

    print("Upcoming fixtures:", len(upcoming))

    return index, upcoming


# ==========================================================
# 2. ROTATING MAPPING SCANNER
# One mapping page per run; progress is saved in GitHub.
# ==========================================================

def collect_mapping_page():

    print("\n[2] ROTATING ODDS MAPPING SCANNER")

    state = load_json(
        STATE_FILE,
        {
            "version": "3.4.2",
            "next_page": 1,
            "total_pages": None,
            "fixture_ids": {},
            "pages_scanned": 0
        }
    )

    page = int(state.get("next_page", 1))
    total_pages = state.get("total_pages")

    if total_pages and page > int(total_pages):
        page = 1

    result = {
        "page": page,
        "records": 0,
        "status": "not_run",
        "error": None
    }

    if quota_protected():
        result["status"] = "quota_protected"
        return state, result

    try:

        payload = api_get(
            "odds/mapping",
            {"page": page}
        )

        rows = payload.get("response", [])
        paging = payload.get("paging", {})

        total_pages = int(paging.get("total", 1))

        for row in rows:

            fixture = row.get("fixture", {})

            if not isinstance(fixture, dict):
                continue

            fixture_id = fixture.get("id")

            if fixture_id is None:
                continue

            key = str(fixture_id)

            state["fixture_ids"][key] = {
                "fixture_id": fixture_id,
                "date": fixture.get("date"),
                "last_seen": now_local().isoformat()
            }

        state["total_pages"] = total_pages
        state["pages_scanned"] = (
            int(state.get("pages_scanned", 0)) + 1
        )

        next_page = page + 1

        if next_page > total_pages:
            next_page = 1

        state["next_page"] = next_page

        result.update({
            "records": len(rows),
            "total_pages": total_pages,
            "status": "success"
        })

        print(
            f"Mapping page {page}/{total_pages}: "
            f"{len(rows)} records"
        )

    except Exception as exc:

        result["status"] = "error"
        result["error"] = str(exc)

        print("Mapping error:", exc)

    save_json(STATE_FILE, state)

    return state, result


# ==========================================================
# 3. CHOOSE THREE UPCOMING MATCH DATES
# ==========================================================

def select_odds_dates(upcoming):

    today = now_local().date()
    date_counts = {}

    for info in upcoming.values():

        timestamp = info.get("timestamp")

        if not timestamp:
            continue

        match_date = datetime.fromtimestamp(
            int(timestamp),
            TZ
        ).date()

        days_ahead = (match_date - today).days

        # Pre-match odds are normally available 1-14 days
        # before kickoff. Do not prioritize today's matches.
        if not (1 <= days_ahead <= FIXTURE_LOOKAHEAD_DAYS):
            continue

        key = match_date.isoformat()
        date_counts[key] = date_counts.get(key, 0) + 1

    ranked_dates = sorted(
        date_counts.items(),
        key=lambda item: (-item[1], item[0])
    )

    selected = [
        {
            "date": date_string,
            "upcoming_fixtures": count
        }
        for date_string, count in ranked_dates[
            :MAX_ODDS_DATE_REQUESTS
        ]
    ]

    print("\nSelected odds dates:")

    for item in selected:
        print(
            item["date"],
            "| Upcoming fixtures:",
            item["upcoming_fixtures"]
        )

    return selected


# ==========================================================
# 4. COLLECT ODDS BY DATE
# One request per selected date, page 1 only.
# Never request page 4 on the free plan.
# ==========================================================

def collect_odds_by_date(selected_dates):

    print("\n[3] COLLECTING DATE-BASED ODDS")

    all_rows = []
    results = []

    for number, item in enumerate(selected_dates, start=1):

        if quota_protected():
            print("Stopping odds scan: quota protection.")
            break

        date_string = item["date"]

        result = {
            "date": date_string,
            "page": 1,
            "records_received": 0,
            "status": "pending",
            "error": None
        }

        print(
            f"\nOdds date {number}/{len(selected_dates)}:",
            date_string
        )

        try:

            payload = api_get(
                "odds",
                {
                    "date": date_string,
                    "timezone": TIMEZONE,
                    "page": 1
                }
            )

            rows = payload.get("response", [])

            result["records_received"] = len(rows)
            result["status"] = (
                "success" if rows else "no_odds"
            )

            result["total_pages"] = payload.get(
                "paging", {}
            ).get("total")

            all_rows.extend(rows)

            print("Odds records:", len(rows))
            print("Provider pages:", result["total_pages"])

        except Exception as exc:

            result["status"] = "error"
            result["error"] = str(exc)

            print("Odds date error:", exc)

        results.append(result)

        if number < len(selected_dates):
            time.sleep(REQUEST_DELAY)

    return all_rows, results


# ==========================================================
# 5. MERGE ODDS WITH FIXTURE DATA
# ==========================================================

def merge_odds(raw_rows, fixture_index, upcoming):

    merged = {}
    audit = []

    unmatched = 0
    invalid = 0

    for row in raw_rows:

        fixture = row.get("fixture", {})
        fixture_id = fixture.get("id")

        if fixture_id is None:
            unmatched += 1
            audit.append({
                "fixture_id": None,
                "linked": False,
                "reason": "MISSING_FIXTURE_ID"
            })
            continue

        key = str(fixture_id)
        info = fixture_index.get(key)

        if info is None:
            unmatched += 1
            audit.append({
                "fixture_id": fixture_id,
                "linked": False,
                "reason": "NOT_IN_14_DAY_FIXTURE_WINDOW"
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
                "fixture_info": info,
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

    linked.sort(
        key=lambda item: int(
            item.get("fixture_info", {}).get("timestamp") or 0
        )
    )

    print("\nRaw odds records:", len(raw_rows))
    print("Linked upcoming matches:", len(linked))
    print("Unmatched records:", unmatched)
    print("Invalid records:", invalid)

    return linked, audit, unmatched, invalid


# ==========================================================
# 6. SAVE OUTPUT
# Compatible with v33_engine.py input schema.
# ==========================================================

def save_output(
    date_string,
    started_at,
    fixtures,
    upcoming,
    mapping_state,
    mapping_result,
    selected_dates,
    odds_results,
    raw_rows,
    linked,
    audit,
    unmatched,
    invalid
):

    print("\n[4] SAVING V3.4.2 DATA")

    date_dir = OUTPUT_DIR / date_string
    date_dir.mkdir(parents=True, exist_ok=True)

    bookmaker_count = sum(
        len(item.get("bookmakers", []))
        for item in linked
    )

    data = {
        "engine": "AI FOOTBALL PREDICTION V3.4.2",
        "source": "API-FOOTBALL",
        "query_method": "14_DAY_FIXTURES_ROTATING_MAPPING_DATE_ODDS",
        "date": date_string,
        "timezone": TIMEZONE,
        "started_at": started_at,
        "finished_at": now_local().isoformat(),

        "configuration": {
            "fixture_lookahead_days": FIXTURE_LOOKAHEAD_DAYS,
            "max_total_requests": MAX_TOTAL_REQUESTS,
            "max_mapping_requests": MAX_MAPPING_REQUESTS,
            "max_odds_date_requests": MAX_ODDS_DATE_REQUESTS,
            "odds_pages_per_date": 1,
            "minimum_remaining_quota": MIN_DAILY_REMAINING
        },

        "mapping": {
            "page_result": mapping_result,
            "next_page": mapping_state.get("next_page"),
            "total_pages": mapping_state.get("total_pages"),
            "unique_fixture_ids_cached": len(
                mapping_state.get("fixture_ids", {})
            ),
            "pages_scanned_total": mapping_state.get(
                "pages_scanned", 0
            )
        },

        "selected_odds_dates": selected_dates,

        "summary": {
            "fixtures_received": len(fixtures),
            "upcoming_fixtures": len(upcoming),
            "mapping_records_received": mapping_result.get(
                "records", 0
            ),
            "odds_dates_selected": len(selected_dates),
            "odds_date_requests_successful": sum(
                1 for item in odds_results
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

        "odds_date_results": odds_results,
        "raw_odds_audit": audit,
        "odds": linked
    }

    latest_file = date_dir / "latest.json"

    timestamp = now_local().strftime("%Y%m%d_%H%M%S")
    archive_file = date_dir / f"v342_{timestamp}.json"

    save_json(latest_file, data)
    save_json(archive_file, data)

    print("Saved:", latest_file)
    print("Archive:", archive_file)

    return data


# ==========================================================
# 7. MAIN
# ==========================================================

def main():

    print("=" * 68)
    print("AI FOOTBALL PREDICTION V3.4.2")
    print("14-DAY FIXTURES + ROTATING MAPPING + DATE-BASED ODDS")
    print("=" * 68)

    if not API_KEY:
        raise RuntimeError("Missing API_FOOTBALL_KEY secret.")

    started_at = now_local().isoformat()
    date_string = now_local().strftime("%Y-%m-%d")

    print("Vietnam date:", date_string)
    print("Maximum requests this run:", MAX_TOTAL_REQUESTS)
    print("API reserve:", MIN_DAILY_REMAINING)

    # 1. Fixtures
    fixtures = collect_fixtures()

    fixture_index, upcoming = build_fixture_index(fixtures)

    # 2. One rotating mapping page
    mapping_state, mapping_result = collect_mapping_page()

    # 3. Select dates with the most upcoming fixtures
    selected_dates = select_odds_dates(upcoming)

    # 4. Query odds for selected future dates
    raw_rows, odds_results = collect_odds_by_date(
        selected_dates
    )

    # 5. Merge
    linked, audit, unmatched, invalid = merge_odds(
        raw_rows,
        fixture_index,
        upcoming
    )

    # 6. Save
    data = save_output(
        date_string,
        started_at,
        fixtures,
        upcoming,
        mapping_state,
        mapping_result,
        selected_dates,
        odds_results,
        raw_rows,
        linked,
        audit,
        unmatched,
        invalid
    )

    print("\n" + "=" * 68)
    print("V3.4.2 COLLECTION SUMMARY")
    print("=" * 68)

    for key, value in data["summary"].items():
        print(f"{key}: {value}")

    print("\n===== ODDS DATE RESULTS =====")

    for item in odds_results:
        print(
            item["date"],
            "|",
            item["status"],
            "| Records:",
            item["records_received"]
        )

    print("\nV3.4.2 COMPLETED")


if __name__ == "__main__":

    try:
        main()
    except Exception as error:
        print("\nV3.4.2 FAILED:")
        print(error)
        raise
