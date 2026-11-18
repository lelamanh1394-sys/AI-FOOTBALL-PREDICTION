import os
import json
import time
import requests

from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# ==========================================================
# AI FOOTBALL PREDICTION V3.3B
# API-FOOTBALL PRE-MATCH ODDS COLLECTOR
#
# IMPORTANT:
# - Odds query uses date, NOT season.
# - No historical odds are attached to current fixtures.
# - No odds are invented when the API returns no data.
# ==========================================================

API_KEY = os.getenv("API_FOOTBALL_KEY")
BASE_URL = "https://v3.football.api-sports.io"

TIMEZONE = "Asia/Ho_Chi_Minh"
TZ = ZoneInfo(TIMEZONE)

OUTPUT_DIR = Path("data/public_odds")

MAX_ODDS_PAGES = 5
REQUEST_DELAY = 1.5
TIMEOUT = 30

session = requests.Session()
session.headers.update({
    "x-apisports-key": API_KEY or ""
})


def now_local():
    return datetime.now(TZ)


def api_get(endpoint, params):
    url = f"{BASE_URL}/{endpoint}"

    response = session.get(
        url,
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
        print(
            f"API quota remaining: {remaining}/{limit}"
        )

    if response.status_code in (401, 403):
        raise RuntimeError(
            f"API authorization failed: HTTP "
            f"{response.status_code}. Check API key "
            "and plan permissions."
        )

    response.raise_for_status()

    payload = response.json()

    errors = payload.get("errors")

    if errors:
        raise RuntimeError(
            f"API returned errors: {errors}"
        )

    return payload


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
            "home": teams.get("home", {}),
            "away": teams.get("away", {})
        }

    return index


def collect_odds_by_date(date_string):
    print("\n[2] COLLECTING ODDS BY DATE")
    print("IMPORTANT: season parameter is NOT used.")

    all_rows = []
    total_pages = 1

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

        rows = payload.get("response", [])
        paging = payload.get("paging", {})

        all_rows.extend(rows)

        try:
            total_pages = int(paging.get("total", 1))
        except (TypeError, ValueError):
            total_pages = 1

        print("Records on page:", len(rows))
        print("Provider total pages:", total_pages)

        if page >= total_pages:
            break

        if page >= MAX_ODDS_PAGES:
            print(
                "PAGE LIMIT REACHED. "
                "Some additional odds pages may remain."
            )
            break

        time.sleep(REQUEST_DELAY)

    return all_rows, total_pages


def merge_odds_rows(rows, fixture_index, current_time):
    merged = {}
    unmatched = 0
    invalid_or_started = 0

    for row in rows:
        fixture = row.get("fixture", {})
        fixture_id = fixture.get("id")

        if fixture_id is None:
            unmatched += 1
            continue

        key = str(fixture_id)
        fixture_info = fixture_index.get(key)

        # Never attach odds to an unknown fixture.
        if fixture_info is None:
            unmatched += 1
            continue

        status = (
            fixture_info.get("status") or {}
        ).get("short")

        if status not in ("NS", "TBD"):
            invalid_or_started += 1
            continue

        timestamp = fixture_info.get("timestamp")

        try:
            kickoff = datetime.fromtimestamp(
                int(timestamp),
                timezone.utc
            )
        except (TypeError, ValueError, OverflowError):
            invalid_or_started += 1
            continue

        if kickoff <= current_time.astimezone(timezone.utc):
            invalid_or_started += 1
            continue

        if key not in merged:
            merged[key] = {
                "fixture_id": fixture_id,
                "fixture_info": fixture_info,
                "bookmakers": []
            }

        existing = {
            str(
                book.get(
                    "id",
                    book.get("name", "")
                )
            )
            for book in merged[key]["bookmakers"]
        }

        for bookmaker in row.get("bookmakers", []):
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

    return (
        list(merged.values()),
        unmatched,
        invalid_or_started
    )


def save_json(path, data):
    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temp = path.with_suffix(".tmp")

    with temp.open("w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2
        )

    temp.replace(path)


def main():
    if not API_KEY:
        raise SystemExit(
            "ERROR: Missing API_FOOTBALL_KEY secret."
        )

    started = now_local()
    date_string = started.strftime("%Y-%m-%d")

    print("=" * 65)
    print("AI FOOTBALL PREDICTION V3.3B")
    print("API-FOOTBALL DATE-BASED ODDS")
    print("Vietnam time:", started.isoformat())
    print("=" * 65)

    try:
        fixtures = collect_fixtures(date_string)

        fixture_index = build_fixture_index(fixtures)

        raw_odds, total_pages = collect_odds_by_date(
            date_string
        )

        linked, unmatched, invalid = merge_odds_rows(
            raw_odds,
            fixture_index,
            now_local()
        )

    except (
        requests.RequestException,
        RuntimeError,
        ValueError
    ) as exc:
        print("\nCOLLECTION FAILED:", exc)
        raise SystemExit(1)

    finished = now_local()

    report = {
        "engine": "AI Football Prediction V3.3B",
        "source": "API-Football",
        "query_method": "date_without_season",
        "date": date_string,
        "timezone": TIMEZONE,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "configuration": {
            "maximum_odds_pages": MAX_ODDS_PAGES,
            "provider_total_pages": total_pages
        },
        "summary": {
            "fixtures_received": len(fixtures),
            "raw_odds_records": len(raw_odds),
            "upcoming_fixtures_with_odds": len(linked),
            "unmatched_fixture_records": unmatched,
            "started_or_invalid_records": invalid
        },
        "odds": linked
    }

    folder = OUTPUT_DIR / date_string

    save_json(
        folder / "latest.json",
        report
    )

    save_json(
        folder / (
            "odds_" +
            started.strftime("%H%M%S") +
            ".json"
        ),
        report
    )

    print("\n===== V3.3B COLLECTION SUMMARY =====")
    print("Fixtures:", len(fixtures))
    print("Raw odds records:", len(raw_odds))
    print("Upcoming matches with odds:", len(linked))
    print("Unmatched records:", unmatched)
    print("Started/invalid records:", invalid)
    print("Saved:", folder / "latest.json")
    print("\nV3.3B COLLECTION COMPLETED")


if __name__ == "__main__":
    main()
