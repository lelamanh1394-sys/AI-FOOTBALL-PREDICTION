
import os
import json
import time
import requests

from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# ==========================================================
# AI FOOTBALL PREDICTION V3.4.3
# DIRECT DATE-BASED ODDS COLLECTOR
# ==========================================================

API_KEY = os.getenv("API_FOOTBALL_KEY")
BASE_URL = "https://v3.football.api-sports.io"

TIMEZONE = "Asia/Ho_Chi_Minh"
TZ = ZoneInfo(TIMEZONE)

OUTPUT_DIR = Path("data/public_odds")

MAX_TOTAL_REQUESTS = 5
MIN_DAILY_REMAINING = 20

# Spread queries across the 1-14 day odds window.
DATE_OFFSETS = [1, 4, 7, 10, 13]

REQUEST_DELAY = 1.2
TIMEOUT = 30

session = requests.Session()
session.headers.update({
    "x-apisports-key": API_KEY or ""
})

REQUESTS_USED = 0
LAST_REMAINING = None


def now_local():
    return datetime.now(TZ)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")

    with temp.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)

    temp.replace(path)


# ==========================================================
# API
# ==========================================================

def api_get(endpoint, params):

    global REQUESTS_USED, LAST_REMAINING

    if REQUESTS_USED >= MAX_TOTAL_REQUESTS:
        raise RuntimeError("Per-run API limit reached")

    if (
        LAST_REMAINING is not None
        and LAST_REMAINING <= MIN_DAILY_REMAINING
    ):
        raise RuntimeError("Daily quota reserve activated")

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
        raise RuntimeError(str(payload["errors"]))

    return payload


# ==========================================================
# SELECT TARGET DATES
# ==========================================================

def select_dates():

    today = now_local().date()

    dates = []

    for offset in DATE_OFFSETS:
        target = today + timedelta(days=offset)

        dates.append({
            "date": target.isoformat(),
            "days_ahead": offset
        })

    print("\n[1] TARGET ODDS DATES")

    for item in dates:
        print(
            item["date"],
            "| Days ahead:",
            item["days_ahead"]
        )

    return dates


# ==========================================================
# COLLECT ODDS DIRECTLY BY DATE
# ==========================================================

def collect_odds(dates):

    print("\n[2] DIRECT DATE-BASED ODDS COLLECTION")

    raw_rows = []
    results = []

    for number, item in enumerate(dates, start=1):

        if REQUESTS_USED >= MAX_TOTAL_REQUESTS:
            break

        if (
            LAST_REMAINING is not None
            and LAST_REMAINING <= MIN_DAILY_REMAINING
        ):
            print("Stopped by quota reserve.")
            break

        date_string = item["date"]

        result = {
            "date": date_string,
            "records_received": 0,
            "status": "pending",
            "error": None
        }

        print(
            f"\nDate {number}/{len(dates)}:",
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

            raw_rows.extend(rows)

            print("Odds records:", len(rows))
            print("Provider pages:", result["total_pages"])

        except Exception as exc:

            result["status"] = "error"
            result["error"] = str(exc)

            print("Date query error:", exc)

            if "429" in str(exc) or "rate limit" in str(exc).lower():
                results.append(result)
                break

        results.append(result)

        if number < len(dates):
            time.sleep(REQUEST_DELAY)

    return raw_rows, results


# ==========================================================
# BUILD V3.3 ENGINE FORMAT
# ==========================================================

def build_fixture_info(row):

    fixture = row.get("fixture") or {}
    teams = row.get("teams") or {}
    league = row.get("league") or {}

    fixture_id = fixture.get("id")
    timestamp = fixture.get("timestamp")
    fixture_date = fixture.get("date")

    if timestamp is None and fixture_date:
        try:
            parsed = datetime.fromisoformat(
                fixture_date.replace("Z", "+00:00")
            )
            timestamp = int(parsed.timestamp())
        except (ValueError, TypeError):
            timestamp = None

    home = teams.get("home") or {}
    away = teams.get("away") or {}

    if isinstance(home, dict):
        home_name = home.get("name")
    else:
        home_name = home

    if isinstance(away, dict):
        away_name = away.get("name")
    else:
        away_name = away

    return {
        "fixture_id": fixture_id,
        "date": fixture_date,
        "timestamp": timestamp,

        # Odds queried for future match dates.
        # Engine independently checks kickoff timestamp.
        "status": {
            "short": "NS"
        },

        "league": {
            "id": league.get("id"),
            "name": league.get("name"),
            "country": league.get("country"),
            "season": league.get("season"),
            "round": league.get("round")
        },

        "home": home_name,
        "away": away_name
    }


def merge_odds(raw_rows):

    merged = {}
    audit = []

    missing_id = 0
    missing_timestamp = 0
    missing_teams = 0

    for row in raw_rows:

        info = build_fixture_info(row)
        fixture_id = info.get("fixture_id")

        if fixture_id is None:
            missing_id += 1
            continue

        if info.get("timestamp") is None:
            missing_timestamp += 1
            continue

        if not info.get("home") or not info.get("away"):
            missing_teams += 1
            continue

        key = str(fixture_id)

        if key not in merged:
            merged[key] = {
                "fixture_id": fixture_id,
                "fixture_info": info,
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
                merged[key]["bookmakers"].append(bookmaker)
                existing.add(book_key)

        audit.append({
            "fixture_id": fixture_id,
            "home": info["home"],
            "away": info["away"],
            "linked": True,
            "bookmakers": len(
                row.get("bookmakers", [])
            )
        })

    linked = list(merged.values())

    linked.sort(
        key=lambda item: int(
            item["fixture_info"].get("timestamp") or 0
        )
    )

    diagnostics = {
        "missing_fixture_id": missing_id,
        "missing_timestamp": missing_timestamp,
        "missing_team_names": missing_teams
    }

    return linked, audit, diagnostics


# ==========================================================
# SAVE OUTPUT
# ==========================================================

def save_output(
    date_string,
    started_at,
    selected_dates,
    date_results,
    raw_rows,
    linked,
    audit,
    diagnostics
):

    date_dir = OUTPUT_DIR / date_string
    date_dir.mkdir(parents=True, exist_ok=True)

    bookmaker_count = sum(
        len(item.get("bookmakers", []))
        for item in linked
    )

    data = {
        "engine": "AI FOOTBALL PREDICTION V3.4.3",
        "source": "API-FOOTBALL",
        "query_method": "DIRECT_DATE_BASED_ODDS",
        "date": date_string,
        "timezone": TIMEZONE,
        "started_at": started_at,
        "finished_at": now_local().isoformat(),

        "configuration": {
            "date_offsets": DATE_OFFSETS,
            "max_total_requests": MAX_TOTAL_REQUESTS,
            "minimum_remaining_quota": MIN_DAILY_REMAINING,
            "odds_page": 1
        },

        "selected_odds_dates": selected_dates,

        "summary": {
            # Compatible with v33_engine.py
            "fixtures_received": len(linked),
            "raw_odds_records": len(raw_rows),
            "linked_upcoming_matches": len(linked),
            "bookmaker_records": bookmaker_count,
            "odds_date_requests_successful": sum(
                1 for item in date_results
                if item.get("status") == "success"
            ),
            "api_requests_used": REQUESTS_USED,
            "api_remaining": LAST_REMAINING,
            **diagnostics
        },

        "odds_date_results": date_results,
        "raw_odds_audit": audit,
        "odds": linked
    }

    latest = date_dir / "latest.json"

    archive = date_dir / (
        "v343_" + now_local().strftime("%Y%m%d_%H%M%S") + ".json"
    )

    save_json(latest, data)
    save_json(archive, data)

    print("\nSaved:", latest)
    print("Archive:", archive)

    return data


# ==========================================================
# MAIN
# ==========================================================

def main():

    print("=" * 68)
    print("AI FOOTBALL PREDICTION V3.4.3")
    print("DIRECT DATE-BASED ODDS COLLECTOR")
    print("=" * 68)

    if not API_KEY:
        raise RuntimeError("Missing API_FOOTBALL_KEY")

    started_at = now_local().isoformat()
    date_string = now_local().strftime("%Y-%m-%d")

    print("Vietnam date:", date_string)
    print("Max requests:", MAX_TOTAL_REQUESTS)

    dates = select_dates()

    raw_rows, date_results = collect_odds(dates)

    linked, audit, diagnostics = merge_odds(raw_rows)

    data = save_output(
        date_string,
        started_at,
        dates,
        date_results,
        raw_rows,
        linked,
        audit,
        diagnostics
    )

    print("\n" + "=" * 68)
    print("V3.4.3 SUMMARY")
    print("=" * 68)

    for key, value in data["summary"].items():
        print(f"{key}: {value}")

    print("\n===== DATE RESULTS =====")

    for item in date_results:
        print(
            item["date"],
            "|",
            item["status"],
            "| Records:",
            item["records_received"]
        )

    print("\n===== MATCHES =====")

    for item in linked:
        info = item["fixture_info"]

        print(
            info["home"],
            "-",
            info["away"],
            "| Bookmakers:",
            len(item["bookmakers"])
        )

    print("\nV3.4.3 COMPLETED")


if __name__ == "__main__":
    main()
