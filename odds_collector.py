import os
import json
import time
import requests

from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# ==========================================================
# AI FOOTBALL PREDICTION V3.3
# PUBLIC ODDS COLLECTOR
# Source: The Odds API official API
# Market: 1X2 / h2h
# ==========================================================

API_KEY = os.getenv("THE_ODDS_API_KEY")
BASE_URL = "https://api.the-odds-api.com/v4"

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
OUTPUT_DIR = Path("data/public_odds")

# Giới hạn danh sách để bảo vệ quota miễn phí.
PREFERRED_SPORTS = {
    "soccer_epl",
    "soccer_uefa_champs_league",
    "soccer_spain_la_liga",
    "soccer_germany_bundesliga",
    "soccer_italy_serie_a",
    "soccer_france_ligue_one",
}

REGIONS = "eu"
MARKETS = "h2h"
ODDS_FORMAT = "decimal"
TIMEOUT = 25

session = requests.Session()


def now_local():
    return datetime.now(TZ)


def request_api(endpoint, params):
    response = session.get(
        f"{BASE_URL}/{endpoint}",
        params=params,
        timeout=TIMEOUT
    )

    print(
        f"HTTP {response.status_code} | "
        f"{endpoint}"
    )

    remaining = response.headers.get(
        "x-requests-remaining"
    )
    used = response.headers.get(
        "x-requests-used"
    )

    if remaining is not None:
        print(
            f"Odds API quota: used={used}, "
            f"remaining={remaining}"
        )

    response.raise_for_status()
    return response.json()


def get_available_sports():
    print("\n[1] CHECKING AVAILABLE SOCCER LEAGUES")

    sports = request_api(
        "sports/",
        {"apiKey": API_KEY}
    )

    available = []

    for sport in sports:
        key = sport.get("key")

        if (
            key in PREFERRED_SPORTS
            and sport.get("active", False)
        ):
            available.append(sport)

    print(
        "Selected active leagues:",
        len(available)
    )

    for sport in available:
        print(
            "-",
            sport.get("title"),
            "|",
            sport.get("key")
        )

    return available


def parse_kickoff(value):
    if not value:
        return None

    try:
        dt = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
        return int(dt.timestamp())
    except (ValueError, TypeError):
        return None


def normalize_event(event, sport):
    kickoff = event.get("commence_time")
    timestamp = parse_kickoff(kickoff)

    if timestamp is None:
        return None

    home = event.get("home_team")
    away = event.get("away_team")

    if not home or not away:
        return None

    normalized_bookmakers = []

    for bookmaker in event.get("bookmakers", []):
        bets = []

        for market in bookmaker.get("markets", []):
            if market.get("key") != "h2h":
                continue

            values = []

            for outcome in market.get("outcomes", []):
                name = outcome.get("name")
                price = outcome.get("price")

                if name in (home, away, "Draw"):
                    values.append({
                        "value": (
                            "Home" if name == home
                            else "Away" if name == away
                            else "Draw"
                        ),
                        "odd": price
                    })

            labels = {
                item["value"] for item in values
            }

            if {"Home", "Draw", "Away"}.issubset(labels):
                bets.append({
                    "name": "Match Winner",
                    "values": values
                })

        if bets:
            normalized_bookmakers.append({
                "id": bookmaker.get("key"),
                "name": bookmaker.get("title", "Unknown"),
                "last_update": bookmaker.get("last_update"),
                "bets": bets
            })

    if not normalized_bookmakers:
        return None

    return {
        "fixture_id": event.get("id"),
        "fixture_info": {
            "fixture_id": event.get("id"),
            "date": kickoff,
            "timestamp": timestamp,
            "status": {"short": "NS"},
            "league": {
                "id": sport.get("key"),
                "name": sport.get("title"),
                "country": None
            },
            "home": {"name": home},
            "away": {"name": away}
        },
        "bookmakers": normalized_bookmakers
    }


def collect_odds(sports):
    print("\n[2] COLLECTING PUBLIC 1X2 ODDS")

    all_events = []
    errors = []

    for index, sport in enumerate(sports, start=1):
        key = sport["key"]

        print(
            f"\nLeague {index}/{len(sports)}: "
            f"{sport.get('title')}"
        )

        try:
            events = request_api(
                f"sports/{key}/odds/",
                {
                    "apiKey": API_KEY,
                    "regions": REGIONS,
                    "markets": MARKETS,
                    "oddsFormat": ODDS_FORMAT,
                    "dateFormat": "iso"
                }
            )

            accepted = 0

            for event in events:
                item = normalize_event(event, sport)

                if item:
                    all_events.append(item)
                    accepted += 1

            print("Events received:", len(events))
            print("Events with valid 1X2:", accepted)

        except requests.RequestException as exc:
            errors.append({
                "sport": key,
                "error": str(exc)
            })
            print("League error:", exc)

        # Tránh gửi yêu cầu liên tục.
        time.sleep(1)

    # Loại bỏ sự kiện trùng ID.
    unique = {}

    for item in all_events:
        unique[str(item["fixture_id"])] = item

    return list(unique.values()), errors


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
            "Missing THE_ODDS_API_KEY GitHub secret."
        )

    started = now_local()

    print("=" * 65)
    print("AI FOOTBALL PREDICTION V3.3")
    print("PUBLIC ODDS COLLECTION")
    print("Vietnam time:", started.isoformat())
    print("=" * 65)

    sports = get_available_sports()

    events, errors = collect_odds(sports)

    finished = now_local()

    report = {
        "engine": "AI Football Prediction V3.3",
        "source": "The Odds API",
        "market": MARKETS,
        "regions": REGIONS,
        "date": started.strftime("%Y-%m-%d"),
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "sports_scanned": [
            item.get("key") for item in sports
        ],
        "summary": {
            "active_sports": len(sports),
            "events_with_valid_odds": len(events),
            "errors": len(errors)
        },
        "errors": errors,
        "odds": events
    }

    day = started.strftime("%Y-%m-%d")
    folder = OUTPUT_DIR / day

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

    print("\n===== ODDS COLLECTION SUMMARY =====")
    print("Sports scanned:", len(sports))
    print("Valid events:", len(events))
    print("Errors:", len(errors))
    print("Saved:", folder / "latest.json")
    print("\nODDS COLLECTION COMPLETED")


if __name__ == "__main__":
    main()
