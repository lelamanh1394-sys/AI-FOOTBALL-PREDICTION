
import os
import json
import time
import requests

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# ==========================================================
# AI FOOTBALL PREDICTION V3.1
# SMART PAGINATION + API QUOTA PROTECTION
# Data collection only - no unverified predictions.
# ==========================================================

API_KEY = os.getenv("API_FOOTBALL_KEY")
BASE_URL = "https://v3.football.api-sports.io"

TIMEZONE = "Asia/Ho_Chi_Minh"
LOCAL_TZ = ZoneInfo(TIMEZONE)

# API protection
MAX_ODDS_PAGES = 20
MIN_DAILY_REMAINING = 20
REQUEST_DELAY_SECONDS = 10.5
REQUEST_TIMEOUT_SECONDS = 30

DATA_DIR = Path("data/online")

SESSION = requests.Session()
LAST_DAILY_REMAINING = None

if not API_KEY:
    raise SystemExit("ERROR: Missing API_FOOTBALL_KEY")

SESSION.headers.update({
    "x-apisports-key": API_KEY
})


class ApiRequestError(Exception):
    pass


def now_local():
    return datetime.now(LOCAL_TZ)


def api_get(endpoint, params):
    global LAST_DAILY_REMAINING

    url = f"{BASE_URL}/{endpoint}"

    try:
        response = SESSION.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS
        )

        print(f"API {endpoint}: HTTP {response.status_code}")

        daily_remaining = response.headers.get(
            "x-ratelimit-requests-remaining"
        )

        daily_limit = response.headers.get(
            "x-ratelimit-requests-limit"
        )

        if daily_remaining is not None:
            LAST_DAILY_REMAINING = int(daily_remaining)

            print(
                "Daily quota remaining: "
                f"{LAST_DAILY_REMAINING}/{daily_limit}"
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


def collect_fixtures(date_string):
    print("\n[1/2] COLLECTING FIXTURES")

    payload = api_get(
        "fixtures",
        {
            "date": date_string,
            "timezone": TIMEZONE
        }
    )

    fixtures = payload.get("response", [])

    print(f"Fixtures found: {len(fixtures)}")

    return fixtures, payload.get("paging", {})


def collect_odds(date_string):
    print("\n[2/2] COLLECTING ODDS")

    all_odds = []
    page_info = []

    total_pages = None
    stop_reason = "Completed all available pages"

    for page in range(1, MAX_ODDS_PAGES + 1):

        # Keep a safe gap between API requests.
        time.sleep(REQUEST_DELAY_SECONDS)

        # Preserve part of the daily API quota.
        if (
            LAST_DAILY_REMAINING is not None
            and LAST_DAILY_REMAINING <= MIN_DAILY_REMAINING
        ):
            stop_reason = (
                "Stopped to preserve daily API quota"
            )
            print(stop_reason)
            break

        try:
            payload = api_get(
                "odds",
                {
                    "date": date_string,
                    "timezone": TIMEZONE,
                    "page": page
                }
            )

        except ApiRequestError as exc:
            stop_reason = str(exc)
            print(f"Odds collection stopped: {stop_reason}")
            break

        rows = payload.get("response", [])
        paging = payload.get("paging", {})

        all_odds.extend(rows)

        page_total = paging.get("total")

        if page_total is not None:
            try:
                total_pages = int(page_total)
            except (ValueError, TypeError):
                pass

        page_info.append({
            "page": page,
            "records": len(rows),
            "total_pages_reported": total_pages
        })

        print(
            f"Odds page {page}: {len(rows)} records "
            f"/ total pages: {total_pages}"
        )

        if not rows:
            stop_reason = "Provider returned an empty page"
            break

        if total_pages is not None and page >= total_pages:
            stop_reason = "All provider pages collected"
            break

        if total_pages is None and len(rows) < 10:
            stop_reason = "Last partial page collected"
            break

    pages_fetched = len(page_info)

    pages_not_fetched = (
        max(0, total_pages - pages_fetched)
        if total_pages is not None
        else None
    )

    pagination = {
        "pages_fetched": pages_fetched,
        "total_pages_reported": total_pages,
        "pages_not_fetched": pages_not_fetched,
        "max_pages_per_run": MAX_ODDS_PAGES,
        "stop_reason": stop_reason
    }

    return all_odds, page_info, pagination


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


def deduplicate_odds(odds_rows):
    """
    Merge duplicate fixture odds records.
    Preserve bookmakers without duplicating bookmaker IDs.
    """

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
                bookmaker.get("id", bookmaker.get("name", ""))
            )

            if book_key not in existing:
                merged[key]["bookmakers"].append(bookmaker)
                existing.add(book_key)

    return list(merged.values())


def link_odds_to_fixtures(odds_rows, fixture_index):
    linked = []
    unmatched = 0

    for row in odds_rows:
        fixture_id = row.get("fixture_id")

        fixture_info = fixture_index.get(str(fixture_id))

        if fixture_info is None:
            unmatched += 1

        linked.append({
            "fixture_id": fixture_id,
            "fixture_info": fixture_info,
            "bookmakers": row.get("bookmakers", [])
        })

    return linked, unmatched


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2
        )


def main():
    started = now_local()

    date_string = started.strftime("%Y-%m-%d")
    timestamp = started.strftime("%Y%m%d_%H%M%S")

    print("=" * 60)
    print("AI FOOTBALL PREDICTION V3.1")
    print("SMART ONLINE SCANNER")
    print(f"Vietnam date: {date_string}")
    print(f"Started: {started.isoformat()}")
    print("=" * 60)

    fixtures, fixture_paging = collect_fixtures(date_string)

    # Respect API request frequency.
    time.sleep(REQUEST_DELAY_SECONDS)

    raw_odds, odds_pages, pagination = collect_odds(
        date_string
    )

    unique_odds = deduplicate_odds(raw_odds)

    fixture_index = build_fixture_index(fixtures)

    linked_odds, unmatched = link_odds_to_fixtures(
        unique_odds,
        fixture_index
    )

    matched_count = len(linked_odds) - unmatched

    coverage = (
        round(matched_count / len(fixtures) * 100, 2)
        if fixtures else 0
    )

    finished = now_local()

    summary_data = {
        "fixtures_found": len(fixtures),
        "odds_raw_records": len(raw_odds),
        "odds_unique_fixtures": len(unique_odds),
        "odds_linked_to_fixtures": matched_count,
        "odds_without_fixture_match": unmatched,
        "odds_coverage_percent": coverage
    }

    result = {
        "engine": "AI Football Prediction V3.1",
        "mode": "online_data_collection",
        "prediction_status": (
            "NOT_ENABLED: calibrated independent probabilities "
            "are required before EV selections."
        ),
        "date": date_string,
        "timezone": TIMEZONE,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "summary": summary_data,
        "quota": {
            "daily_remaining_after_last_request":
                LAST_DAILY_REMAINING,
            "minimum_daily_reserve": MIN_DAILY_REMAINING
        },
        "fixture_paging": fixture_paging,
        "odds_pagination": pagination,
        "odds_pages": odds_pages,
        "fixtures": fixtures,
        "odds": linked_odds
    }

    day_dir = DATA_DIR / date_string

    snapshot_path = (
        day_dir / f"scan_{timestamp}.json"
    )

    save_json(snapshot_path, result)
    save_json(day_dir / "latest.json", result)

    summary = {
        "engine": result["engine"],
        "date": date_string,
        "finished_at": finished.isoformat(),
        **summary_data,
        "odds_pagination": pagination,
        "prediction_status": result["prediction_status"],
        "snapshot": str(snapshot_path)
    }

    save_json(day_dir / "summary.json", summary)

    print("\n===== V3.1 SCAN SUMMARY =====")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    print("\nSCAN COMPLETED")


if __name__ == "__main__":
    main()
