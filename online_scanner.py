
import os
import json
import time
import requests
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# ==========================================================
# AI FOOTBALL PREDICTION - V3 ONLINE SCANNER
# Data collection only: no unverified betting predictions.
# ==========================================================

API_KEY = os.getenv("API_FOOTBALL_KEY")
BASE_URL = "https://v3.football.api-sports.io"

TIMEZONE = "Asia/Ho_Chi_Minh"
LOCAL_TZ = ZoneInfo(TIMEZONE)

# Safe limits: fixture request + at most 3 odds pages per run.
MAX_ODDS_PAGES = 3
REQUEST_DELAY_SECONDS = 10.5
REQUEST_TIMEOUT_SECONDS = 30

DATA_DIR = Path("data/online")
SESSION = requests.Session()

if not API_KEY:
    raise SystemExit(
        "ERROR: Missing GitHub Secret API_FOOTBALL_KEY"
    )

SESSION.headers.update({
    "x-apisports-key": API_KEY
})


def now_local():
    return datetime.now(LOCAL_TZ)


def api_get(endpoint, params):
    """Make one API request and report provider quota information."""
    url = f"{BASE_URL}/{endpoint}"

    try:
        response = SESSION.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS
        )

        print(f"API {endpoint}: HTTP {response.status_code}")

        remaining = response.headers.get(
            "x-ratelimit-requests-remaining"
        )
        limit = response.headers.get(
            "x-ratelimit-requests-limit"
        )

        if remaining is not None:
            print(f"Daily API quota remaining: {remaining}/{limit}")

        response.raise_for_status()
        payload = response.json()

        errors = payload.get("errors")
        if errors:
            raise RuntimeError(f"API error: {errors}")

        return payload

    except requests.RequestException as exc:
        raise RuntimeError(
            f"Network/API request failed: {exc}"
        ) from exc


def collect_fixtures(date_string):
    print("\n[1/2] Collecting fixtures...")

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
    print("\n[2/2] Collecting odds...")

    all_odds = []
    page_info = []

    for page in range(1, MAX_ODDS_PAGES + 1):
        # Respect the provider's free-plan request frequency.
        time.sleep(REQUEST_DELAY_SECONDS)

        payload = api_get(
            "odds",
            {
                "date": date_string,
                "timezone": TIMEZONE,
                "page": page
            }
        )

        rows = payload.get("response", [])
        paging = payload.get("paging", {})

        all_odds.extend(rows)
        page_info.append({
            "page": page,
            "returned": len(rows),
            "paging": paging
        })

        total_pages = int(paging.get("total", page))

        print(
            f"Odds page {page}: {len(rows)} records "
            f"(provider pages: {total_pages})"
        )

        if not rows or page >= total_pages:
            break

    return all_odds, page_info


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


def link_odds_to_fixtures(odds_rows, fixture_index):
    linked = []
    unmatched = 0

    for row in odds_rows:
        fixture = row.get("fixture", {})
        fixture_id = fixture.get("id")
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
    print("AI FOOTBALL PREDICTION V3 - ONLINE SCANNER")
    print(f"Vietnam date: {date_string}")
    print(f"Started: {started.isoformat()}")
    print("=" * 60)

    fixtures, fixture_paging = collect_fixtures(date_string)

    # Keep a gap between API requests.
    time.sleep(REQUEST_DELAY_SECONDS)

    odds_rows, odds_pages = collect_odds(date_string)

    fixture_index = build_fixture_index(fixtures)
    linked_odds, unmatched = link_odds_to_fixtures(
        odds_rows,
        fixture_index
    )

    finished = now_local()

    result = {
        "engine": "AI Football Prediction V3",
        "mode": "online_data_collection",
        "prediction_status": (
            "NOT_ENABLED: independent calibrated probabilities "
            "are required before EV selections can be made."
        ),
        "date": date_string,
        "timezone": TIMEZONE,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "summary": {
            "fixtures_found": len(fixtures),
            "odds_records_found": len(odds_rows),
            "odds_records_linked": len(odds_rows) - unmatched,
            "odds_records_without_fixture_match": unmatched,
            "max_odds_pages": MAX_ODDS_PAGES
        },
        "fixture_paging": fixture_paging,
        "odds_pages": odds_pages,
        "fixtures": fixtures,
        "odds": linked_odds
    }

    day_dir = DATA_DIR / date_string

    snapshot_path = day_dir / f"scan_{timestamp}.json"
    latest_path = day_dir / "latest.json"

    save_json(snapshot_path, result)
    save_json(latest_path, result)

    summary = {
        "engine": result["engine"],
        "date": date_string,
        "finished_at": finished.isoformat(),
        "fixtures_found": len(fixtures),
        "odds_records_found": len(odds_rows),
        "odds_records_linked": len(odds_rows) - unmatched,
        "prediction_status": result["prediction_status"],
        "snapshot": str(snapshot_path)
    }

    save_json(day_dir / "summary.json", summary)

    print("\n===== V3 SCAN SUMMARY =====")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\nSCAN COMPLETED")


if __name__ == "__main__":
    main()
