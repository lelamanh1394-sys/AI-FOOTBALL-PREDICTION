
import os
import json
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from datetime import datetime, timezone

API_URL = "https://v3.football.api-sports.io"
API_KEY = os.getenv("API_FOOTBALL_KEY")

QUEUE_FILE = Path("data/catalog/history_queue.json")
PROGRESS_FILE = Path("data/catalog/history_progress.json")
OUTPUT_DIR = Path("data/history")

MAX_REQUESTS = 3
MIN_REMAINING = 10

def load_json(path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def api_get(league_id, season):
    params = urllib.parse.urlencode({
        "league": league_id,
        "season": season
    })

    url = f"{API_URL}/fixtures?{params}"

    request = urllib.request.Request(
        url,
        headers={"x-apisports-key": API_KEY},
        method="GET"
    )

    with urllib.request.urlopen(request, timeout=60) as response:
        data = json.loads(response.read().decode("utf-8"))
        remaining = response.headers.get(
            "x-ratelimit-requests-remaining"
        )

    if data.get("errors"):
        raise RuntimeError(str(data["errors"]))

    return data, remaining

def task_key(task):
    return f'{task["league_id"]}_{task["season"]}'

def main():
    if not API_KEY:
        raise RuntimeError("Missing API_FOOTBALL_KEY")

    if not QUEUE_FILE.exists():
        raise FileNotFoundError("Missing history_queue.json")

    queue = load_json(QUEUE_FILE, [])
    progress = load_json(PROGRESS_FILE, {})

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    requests_used = 0
    completed = 0
    empty = 0
    errors = 0

    print("=" * 55)
    print("AI FOOTBALL HISTORY COLLECTOR V2.3")
    print("Queue tasks:", len(queue))
    print("Maximum API calls:", MAX_REQUESTS)
    print("=" * 55)

    for task in queue:
        if requests_used >= MAX_REQUESTS:
            print("Reached daily history request limit.")
            break

        key = task_key(task)
        output_file = OUTPUT_DIR / (
            f'league_{task["league_id"]}_'
            f'season_{task["season"]}.json'
        )

        if progress.get(key, {}).get("status") in [
            "completed", "no_data"
        ]:
            continue

        if output_file.exists():
            progress[key] = {
                "status": "completed",
                "updated_at": datetime.now(
                    timezone.utc
                ).isoformat(),
                "note": "Existing file detected"
            }
            continue

        print(
            "\nDownloading:",
            task["country"],
            "-",
            task["league_name"],
            "-",
            task["season"]
        )

        try:
            data, remaining = api_get(
                task["league_id"],
                task["season"]
            )
            requests_used += 1

            results = data.get("results", 0)

            save_json(output_file, data)

            status = "completed" if results > 0 else "no_data"

            progress[key] = {
                "status": status,
                "results": results,
                "updated_at": datetime.now(
                    timezone.utc
                ).isoformat()
            }

            if results > 0:
                completed += 1
            else:
                empty += 1

            print("Fixtures returned:", results)
            print("API requests remaining:", remaining)

            if remaining is not None:
                try:
                    if int(remaining) <= MIN_REMAINING:
                        print("Safety stop: API quota is low.")
                        break
                except ValueError:
                    pass

        except urllib.error.HTTPError as exc:
            requests_used += 1
            errors += 1
            progress[key] = {
                "status": "retry",
                "error": f"HTTP {exc.code}",
                "updated_at": datetime.now(
                    timezone.utc
                ).isoformat()
            }
            print("HTTP error:", exc.code)
            break

        except Exception as exc:
            requests_used += 1
            errors += 1
            progress[key] = {
                "status": "retry",
                "error": str(exc),
                "updated_at": datetime.now(
                    timezone.utc
                ).isoformat()
            }
            print("Collection error:", exc)
            break

    save_json(PROGRESS_FILE, progress)

    finished = sum(
        1 for item in progress.values()
        if item.get("status") in ["completed", "no_data"]
    )

    print("\n===== V2.3 SUMMARY =====")
    print("API calls used:", requests_used)
    print("New seasons downloaded:", completed)
    print("Seasons with no data:", empty)
    print("Errors:", errors)
    print("Total finished tasks:", finished)
    print("Remaining queue:", max(0, len(queue) - finished))
    print("HISTORY COLLECTION COMPLETED")

if __name__ == "__main__":
    main()
