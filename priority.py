
import json
import csv
from pathlib import Path
from collections import defaultdict, Counter, deque

SOURCE = Path("data/catalog/league_catalog.json")
OUTPUT = Path("data/catalog")

# Gioi han mua du lieu theo thong bao API Free
MIN_SEASON = 2022
MAX_SEASON = 2024

WEIGHTS = {
    "events": 15,
    "lineups": 15,
    "statistics_fixtures": 25,
    "standings": 15,
    "players": 10,
    "top_scorers": 5,
    "top_assists": 3,
    "top_cards": 2,
}


def safe_year(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def safe_id(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value).strip() or None


def clean_text(value, fallback):
    if value is None:
        return fallback
    value = str(value).strip()
    return value if value else fallback


def calculate_score(item):
    coverage = item.get("coverage") or {}
    fixtures = coverage.get("fixtures") or {}

    score = 0
    details = {}

    for key, weight in WEIGHTS.items():
        if key in ("events", "lineups", "statistics_fixtures"):
            supported = fixtures.get(key) is True
        else:
            supported = coverage.get(key) is True

        details[key] = supported
        if supported:
            score += weight

    seasons = item.get("seasons") or []
    season_bonus = min(len(seasons), 10)
    score += season_bonus

    details["season_count"] = len(seasons)
    details["season_bonus"] = season_bonus

    if score >= 65:
        tier = "A"
    elif score >= 45:
        tier = "B"
    else:
        tier = "C"

    return score, tier, details


def build_country_queue(country_tasks):
    """
    Can bang trong mot quoc gia:
    1. Luân phien cac loai giai.
    2. Trong moi loai giai, luan phien cac giai dau.
    3. Trong moi giai, tai mua gan nhat truoc.
    """

    type_queues = {}

    for league_type, league_tasks in country_tasks.items():
        by_league = defaultdict(list)

        for task in league_tasks:
            by_league[task["league_id"]].append(task)

        # Moi giai dau thanh mot hang doi rieng.
        league_queues = []

        for league_id, tasks in by_league.items():
            tasks.sort(key=lambda x: x["season"], reverse=True)

            # Diem chi la tieu chi phu trong cung quoc gia/loai giai.
            tasks.sort(
                key=lambda x: x["priority_score"],
                reverse=True
            )

            league_queues.append({
                "league_id": league_id,
                "score": max(x["priority_score"] for x in tasks),
                "name": tasks[0]["league_name"],
                "tasks": deque(tasks),
            })

        league_queues.sort(
            key=lambda x: (x["score"], x["name"]),
            reverse=True
        )

        # Luân phiên từng giải trong cùng loại giải.
        type_stream = []

        while any(q["tasks"] for q in league_queues):
            for q in league_queues:
                if q["tasks"]:
                    type_stream.append(q["tasks"].popleft())

        type_queues[league_type] = deque(type_stream)

    # Luân phiên League, Cup va cac loai khac.
    result = []

    ordered_types = sorted(type_queues.keys())

    while any(type_queues[t] for t in ordered_types):
        for league_type in ordered_types:
            if type_queues[league_type]:
                result.append(type_queues[league_type].popleft())

    return result


def build_global_fair_queue(tasks):
    """
    Luân phiên giữa các quốc gia:
    mỗi vòng, mỗi quốc gia đóng góp tối đa một nhiệm vụ.
    """

    by_country = defaultdict(lambda: defaultdict(list))

    for task in tasks:
        country = task["country"]
        league_type = task["league_type"]
        by_country[country][league_type].append(task)

    country_queues = {}

    for country, grouped_tasks in by_country.items():
        country_queues[country] = deque(
            build_country_queue(grouped_tasks)
        )

    result = []
    countries = sorted(country_queues.keys())
    round_number = 0

    while any(country_queues[c] for c in countries):
        round_number += 1

        for country in countries:
            if country_queues[country]:
                task = country_queues[country].popleft()
                task["country_round"] = round_number
                result.append(task)

    for index, task in enumerate(result, start=1):
        task["queue_order"] = index

    return result


def main():
    if not SOURCE.exists():
        raise FileNotFoundError("Missing league_catalog.json")

    with SOURCE.open("r", encoding="utf-8") as f:
        leagues = json.load(f)

    ranked = []
    tasks = []
    seen_tasks = set()

    for item in leagues:
        league_id = safe_id(item.get("league_id"))
        league_name = clean_text(
            item.get("league_name"),
            "Unknown league"
        )
        country = clean_text(
            item.get("country"),
            "Unknown country"
        )
        league_type = clean_text(
            item.get("league_type"),
            "Other"
        )

        score, tier, details = calculate_score(item)
        seasons = item.get("seasons") or []

        ranked.append({
            "league_id": league_id,
            "league_name": league_name,
            "country": country,
            "league_type": league_type,
            "priority_score": score,
            "priority_tier": tier,
            "latest_season": item.get("latest_season"),
            "season_count": len(seasons),
            "coverage_details": details,
        })

        if league_id is None:
            continue

        for season in seasons:
            year = safe_year(season.get("year"))

            if not (MIN_SEASON <= year <= MAX_SEASON):
                continue

            # Chong lap duoc loai bo theo cap league_id + season.
            task_key = (str(league_id), year)

            if task_key in seen_tasks:
                continue

            seen_tasks.add(task_key)

            tasks.append({
                "league_id": league_id,
                "league_name": league_name,
                "country": country,
                "league_type": league_type,
                "season": year,
                "priority_score": score,
                "priority_tier": tier,
                "status": "pending",
            })

    ranked.sort(
        key=lambda x: (
            x["priority_score"],
            x["season_count"]
        ),
        reverse=True
    )

    # Tao hang doi can bang tren toan bo quoc gia.
    queue = build_global_fair_queue(tasks)

    OUTPUT.mkdir(parents=True, exist_ok=True)

    with (OUTPUT / "priority_leagues.json").open(
        "w", encoding="utf-8"
    ) as f:
        json.dump(ranked, f, ensure_ascii=False, indent=2)

    with (OUTPUT / "history_queue.json").open(
        "w", encoding="utf-8"
    ) as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)

    with (OUTPUT / "priority_leagues.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as f:
        fields = [
            "league_id",
            "league_name",
            "country",
            "league_type",
            "priority_score",
            "priority_tier",
            "latest_season",
            "season_count",
        ]

        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()

        for item in ranked:
            writer.writerow({
                key: item.get(key) for key in fields
            })

    tier_counts = Counter(
        x["priority_tier"] for x in ranked
    )

    country_counts = Counter(
        x["country"] for x in queue
    )

    type_counts = Counter(
        x["league_type"] for x in queue
    )

    season_counts = Counter(
        x["season"] for x in queue
    )

    print("===== AI FOOTBALL PRIORITY V2.5 =====")
    print("Scheduling: GLOBAL FAIR ROTATION")
    print("Total leagues:", len(ranked))
    print("Countries in catalogue:", len(set(
        x["country"] for x in ranked
    )))
    print("Countries in history queue:", len(country_counts))
    print("Historical tasks:", len(queue))
    print("Allowed seasons:", MIN_SEASON, "-", MAX_SEASON)
    print("Priority groups:", dict(tier_counts))
    print("Tasks by competition type:", dict(type_counts))
    print("Tasks by season:", dict(sorted(season_counts.items())))

    print("\nTOP 10 PRIORITY LEAGUES")
    for item in ranked[:10]:
        print(
            item["priority_tier"],
            item["priority_score"],
            item["country"],
            "-",
            item["league_name"]
        )

    print("\nFIRST 20 GLOBAL QUEUE TASKS")
    for task in queue[:20]:
        print(
            task["queue_order"],
            task["country"],
            "-",
            task["league_name"],
            "-",
            task["season"]
        )

    print("\nGLOBAL FAIR QUEUE READY")


if __name__ == "__main__":
    main()
