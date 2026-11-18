
import os
import json
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

API_URL = "https://v3.football.api-sports.io"
API_KEY = os.getenv("API_FOOTBALL_KEY")

if not API_KEY:
    raise RuntimeError("Missing API_FOOTBALL_KEY secret")

def api_get(endpoint, params=None):
    query = urllib.parse.urlencode(params or {})
    url = f"{API_URL}/{endpoint}"
    if query:
        url += f"?{query}"

    request = urllib.request.Request(
        url,
        headers={"x-apisports-key": API_KEY},
        method="GET"
    )

    with urllib.request.urlopen(request, timeout=40) as response:
        data = json.loads(response.read().decode("utf-8"))

        remaining = response.headers.get(
            "x-ratelimit-requests-remaining", "unknown"
        )
        print(f"API requests remaining: {remaining}")

    if data.get("errors"):
        raise RuntimeError(f"API error: {data['errors']}")

    return data

def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)

    print(f"Saved: {path}")

def main():
    today = datetime.now(
        ZoneInfo("Asia/Ho_Chi_Minh")
    ).strftime("%Y-%m-%d")

    print("=" * 55)
    print("AI FOOTBALL DATA COLLECTOR V2")
    print("Collection date:", today)
    print("=" * 55)

    # 1. Collect global league database
    print("\nDownloading global leagues...")
    leagues = api_get("leagues")

    save_json("data/leagues/all_leagues.json", leagues)

    print("Leagues returned:", len(leagues.get("response", [])))

    # 2. Collect fixtures for Vietnam's current date
    print("\nDownloading worldwide fixtures...")
    fixtures = api_get(
        "fixtures",
        {
            "date": today,
            "timezone": "Asia/Ho_Chi_Minh"
        }
    )

    save_json(f"data/fixtures/{today}.json", fixtures)

    print("Fixtures returned:", len(fixtures.get("response", [])))

    print("\nCOLLECTION COMPLETED")
    print("Date:", today)
    print("Version: V2")

if __name__ == "__main__":
    main()
