
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

# V2.6: gioi han thu thap moi lan chay
MAX_REQUESTS = 40

# Dung som neu quota con 15 request
MIN_REMAINING = 15

FINISHED_STATUSES = ["completed", "no_data"]
RESTRICTED_STATUS = "plan_restricted"


def now():
    return datetime.now(timezone.utc).isoformat()


def load_json(path, default):
    if not path.exists():
        return default

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = path.with_suffix(path.suffix + ".tmp")

    with temp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    temp_path.replace(path)


def task_key(task):
    return f'{task["league_id"]}_{task["season"]}'


def output_path(task):
    return OUTPUT_DIR / (
        f'league_{task["league_id"]}_'
        f'season_{task["season"]}.json'
    )


def is_plan_restriction(message):
    text = str(message).lower()

    keywords = [
        "free plans do not have access",
        "plan does not have access",
        "not available for your plan",
        "subscription",
        "season not allowed",
        "access to this season"
    ]

    return any(word in text for word in keywords)


def get_remaining(headers):
    if headers is None:
        return None

    value = headers.get("x-ratelimit-requests-remaining")

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
        body = response.read().decode("utf-8")
        data = json.loads(body)
        remaining = get_remaining(response.headers)

    if data.get("errors"):
        message = str(data["errors"])

        if is_plan_restriction(message):
            raise PermissionError(message)

        if "limit" in message.lower() or "quota" in message.lower():
            raise RuntimeError(f"API quota warning: {message}")

        raise RuntimeError(message)

    return data, remaining


def save_progress(progress):
    save_json(PROGRESS_FILE, progress)


def mark_retry(progress, key, error):
    progress[key] = {
        "status": "retry",
        "error": str(error)[:1000],
        "updated_at": now()
    }

    save_progress(progress)


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
    restricted = 0
    errors = 0
    already_saved = 0
    safety_stopped = False

    print("=" * 64)
    print("AI FOOTBALL HISTORY COLLECTOR V2.6")
    print("Queue tasks:", len(queue))
    print("Maximum API calls:", MAX_REQUESTS)
    print("Minimum remaining quota:", MIN_REMAINING)
    print("Global fair queue: ON")
    print("Smart plan restriction handling: ON")
    print("Progress auto-save: ON")
    print("Duplicate protection: ON")
    print("=" * 64)

    for task in queue:
        key = task_key(task)
        file_path = output_path(task)

        status = progress.get(key, {}).get("status")

        # Bo qua nhiem vu da hoan thanh.
        if status in FINISHED_STATUSES:
            continue

        # Bo qua mua bi gioi han boi goi API.
        if status == RESTRICTED_STATUS:
            continue

        # Neu file da ton tai, khong tai lai.
        if file_path.exists():
            progress[key] = {
                "status": "completed",
                "updated_at": now(),
                "note": "Existing file detected"
            }

            already_saved += 1
            save_progress(progress)
            continue

        # Gioi han so request moi lan chay.
        if requests_used >= MAX_REQUESTS:
            print("\nReached maximum API calls.")
            break

        print(
            "\nDownloading:",
            task.get("country", "Unknown"),
            "-",
            task.get("league_name", "Unknown league"),
            "-",
            task.get("season")
        )

        try:
            data, remaining = api_get(
                task["league_id"],
                task["season"]
            )

            requests_used += 1

            results = data.get("results", 0)

            # Luu du lieu truoc khi cap nhat tien do.
            save_json(file_path, data)

            status = "completed" if results > 0 else "no_data"

            progress[key] = {
                "status": status,
                "results": results,
                "updated_at": now()
            }

            if results > 0:
                completed += 1
            else:
                empty += 1

            print("Fixtures returned:", results)
            print("API requests remaining:", remaining)

            save_progress(progress)

            # Dung neu quota con thap.
            if remaining is not None and remaining <= MIN_REMAINING:
                print("Safety stop: API quota reached safety threshold.")
                safety_stopped = True
                break

        except PermissionError as exc:
            requests_used += 1
            restricted += 1

            progress[key] = {
                "status": RESTRICTED_STATUS,
                "error": str(exc)[:1000],
                "updated_at": now()
            }

            save_progress(progress)

            print("PLAN RESTRICTION:", exc)
            print("Skipping this season. Continuing queue...")

        except urllib.error.HTTPError as exc:
            requests_used += 1
            errors += 1

            try:
                body = exc.read().decode(
                    "utf-8",
                    errors="replace"
                )
            except Exception:
                body = ""

            if exc.code == 403 and is_plan_restriction(body):
                restricted += 1

                progress[key] = {
                    "status": RESTRICTED_STATUS,
                    "error": body[:1000],
                    "updated_at": now()
                }

                save_progress(progress)

                print("PLAN RESTRICTION: HTTP 403")
                print("Skipping this season. Continuing queue...")
                continue

            mark_retry(
                progress,
                key,
                f"HTTP {exc.code}: {body[:500]}"
            )

            print("HTTP error:", exc.code)

            if exc.code == 429:
                print("API rate limit reached. Stopping.")
            else:
                print("Stopping to protect API quota.")

            break

        except Exception as exc:
            requests_used += 1
            errors += 1

            mark_retry(progress, key, exc)

            print("Collection error:", exc)
            print("Stopping. This task can be retried later.")
            break

    save_progress(progress)

    finished = sum(
        1 for item in progress.values()
        if item.get("status") in FINISHED_STATUSES
    )

    restricted_total = sum(
        1 for item in progress.values()
        if item.get("status") == RESTRICTED_STATUS
    )

    pending = 0

    for task in queue:
        key = task_key(task)
        status = progress.get(key, {}).get("status")

        if status in FINISHED_STATUSES + [RESTRICTED_STATUS]:
            continue

        if output_path(task).exists():
            continue

        pending += 1

    print("\n" + "=" * 64)
    print("AI FOOTBALL HISTORY COLLECTOR V2.6 SUMMARY")
    print("=" * 64)

    print("API calls used:", requests_used)
    print("Maximum API calls:", MAX_REQUESTS)
    print("New seasons downloaded:", completed)
    print("Seasons with no data:", empty)
    print("Plan restricted this run:", restricted)
    print("Total plan restricted:", restricted_total)
    print("Existing files detected:", already_saved)
    print("Errors:", errors)
    print("Total finished tasks:", finished)
    print("Remaining pending tasks:", pending)
    print("Safety stopped:", safety_stopped)

    print("=" * 64)
    print("HISTORY COLLECTION V2.6 COMPLETED")


if __name__ == "__main__":
    main()
