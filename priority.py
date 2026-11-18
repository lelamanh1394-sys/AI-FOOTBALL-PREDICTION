
import json
import csv
from pathlib import Path
from collections import defaultdict, Counter, deque

SOURCE = Path("data/catalog/league_catalog.json")
OUTPUT = Path("data/catalog")

# Gioi han mua du lieu theo gioi han API Free da kiem tra
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
    if isinstance(value, dict):
        value = value.get("year", value.get("season"))

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
        value = str(value).strip()
        return value or None


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

    1. Luân phiên các loại giải.
    2. Trong mỗi loại giải, luân phiên các giải đấu.
    3. Trong mỗi giải, ưu tiên mùa mới trước.
    4. Diem uu tien chi dung de sap xep phu.
    """

    type_queues = {}

    for league_type, league_tasks in country_tasks.items():
        by_league = defaultdict(list)

        for task in league_tasks:
            by_league[task["league_id"]].append(task)

        league_queues = []

        for league_id, tasks in by_league.items():
            # Mua moi hon duoc dua len truoc.
            tasks.sort(
                key=lambda x: x["season"],
                reverse=True
            )

            league_queues.append({
                "league_id": league_id,
                "score": max(
                    x["priority_score"] for x in tasks
                ),
                "name": tasks[0]["league_name"],
                "tasks": deque(tasks),
            })

        # Diem cao hon duoc uu tien trong cung loai giai.
        league_queues.sort(
            key=lambda x: (x["score"], x["name"]),
            reverse=True
        )

        # Moi giai dau dong gop toi da mot mua moi moi vong.
        type_stream = []

        while any(q["tasks"] for q in league_queues):
            for q in league_queues:
                if q["tasks"]:
                    type_stream.append(q["tasks"].popleft())

        type_queues[league_type] = deque(type_stream)

    # Luân phiên giữa League, Cup và mọi loại giải khác.
    result = []
    ordered_types = sorted(type_queues.keys())

    while any(type_queues[t] for t in ordered_types):
        for league_type in ordered_types:
            if type_queues[league_type]:
                result.append(
                    type_queues[league_type].popleft()
                )

    return result


def build_global_fair_queue(tasks):
    """
    Luân phiên công bằng giữa các quốc gia.

    Mỗi vòng:
    - Mỗi quốc gia đóng góp tối đa một nhiệm vụ.
    - Không để một quốc gia có nhiều giải đấu chiếm hết hàng đợi.
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


def build_summary(ranked, queue):
    countries = set()
    leagues = set()
    country_task_counts = Counter()
    type_task_counts = Counter()
    season_task_counts = Counter()

    country_leagues = defaultdict(set)
    type_leagues = defaultdict(set)

    for task in queue:
        countries.add(task["country"])
        leagues.add(str(task["league_id"]))

        country_task_counts[task["country"]] += 1
        type_task_counts[task["league_type"]] += 1
        season_task_counts[str(task["season"])] += 1

        country_leagues[task["country"]].add(
            str(task["league_id"])
        )

        type_leagues[task["league_type"]].add(
            str(task["league_id"])
        )

    return {
        "version": "V2.5",
        "scheduling": "GLOBAL FAIR ROTATION",
        "allowed_seasons": {
            "min": MIN_SEASON,
            "max": MAX_SEASON,
        },
        "total_catalogue_leagues": len(ranked),
        "total_catalogue_countries": len(
            set(x["country"] for x in ranked)
        ),
        "countries_in_queue": len(countries),
        "unique_leagues_in_queue": len(leagues),
        "historical_tasks": len(queue),
        "tasks_by_country": dict(
            sorted(country_task_counts.items())
        ),
        "unique_leagues_by_country": {
            country: len(values)
            for country, values in sorted(
                country_leagues.items()
            )
        },
        "tasks_by_competition_type": dict(
            sorted(type_task_counts.items())
        ),
        "unique_leagues_by_competition_type": {
            league_type: len(values)
            for league_type, values in sorted(
                type_leagues.items()
            )
        },
        "tasks_by_season": dict(
            sorted(season_task_counts.items())
        ),
        "priority_tiers": dict(
            Counter(x["priority_tier"] for x in ranked)
        ),
    }


def main():
    if not SOURCE.exists():
        raise FileNotFoundError(
            "Missing data/catalog/league_catalog.json"
        )

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
            year = safe_year(season)

            if not (MIN_SEASON <= year <= MAX_SEASON):
                continue

            # Moi cap league_id + season chi xuat hien mot lan.
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

    # Xay dung hang doi luan phien toan cau.
    queue = build_global_fair_queue(tasks)

    OUTPUT.mkdir(parents=True, exist_ok=True)

    # Danh muc giai dau da cham diem.
    with (OUTPUT / "priority_leagues.json").open(
        "w", encoding="utf-8"
    ) as f:
        json.dump(
            ranked,
            f,
            ensure_ascii=False,
            indent=2
        )

    # Hang doi tai du lieu lich su.
    with (OUTPUT / "history_queue.json").open(
        "w", encoding="utf-8"
    ) as f:
        json.dump(
            queue,
            f,
            ensure_ascii=False,
            indent=2
        )

    # Bao cao danh muc CSV.
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

        writer = csv.DictWriter(
            f,
            fieldnames=fields
        )

        writer.writeheader()

        for item in ranked:
            writer.writerow({
                key: item.get(key)
                for key in fields
            })

    # Bao cao phan bo hang doi.
    summary = build_summary(ranked, queue)

    with (OUTPUT / "history_queue_summary.json").open(
        "w", encoding="utf-8"
    ) as f:
        json.dump(
            summary,
            f,
            ensure_ascii=False,
            indent=2
        )

    print("===== AI FOOTBALL PRIORITY V2.5 =====")
    print("Scheduling: GLOBAL FAIR ROTATION")
    print(
        "Total catalogue leagues:",
        summary["total_catalogue_leagues"]
    )
    print(
        "Total catalogue countries:",
        summary["total_catalogue_countries"]
    )
    print(
        "Countries in history queue:",
        summary["countries_in_queue"]
    )
    print(
        "Unique leagues in history queue:",
        summary["unique_leagues_in_queue"]
    )
    print(
        "Historical tasks:",
        summary["historical_tasks"]
    )
    print(
        "Allowed seasons:",
        MIN_SEASON,
        "-",
        MAX_SEASON
    )

    print(
        "Priority groups:",
        summary["priority_tiers"]
    )

    print(
        "Tasks by competition type:",
        summary["tasks_by_competition_type"]
    )

    print(
        "Tasks by season:",
        summary["tasks_by_season"]
    )

    print("\nTOP 10 PRIORITY LEAGUES")

    for item in ranked[:10]:
        print(
            item["priority_tier"],
            item["priority_score"],
            item["country"],
            "-",
            item["league_name"]
        )

    print("\nFIRST 30 GLOBAL QUEUE TASKS")

    for task in queue[:30]:
        print(
            task["queue_order"],
            task["country"],
            "-",
            task["league_name"],
            "-",
            task["league_type"],
            "-",
            task["season"]
        )

    print("\nGLOBAL FAIR QUEUE READY")
    print("SUMMARY SAVED")


if __name__ == "__main__":
    main()
