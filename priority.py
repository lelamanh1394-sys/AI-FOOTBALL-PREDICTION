
import json
import csv
from pathlib import Path

SOURCE = Path("data/catalog/league_catalog.json")
OUTPUT = Path("data/catalog")

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

def main():
    if not SOURCE.exists():
        raise FileNotFoundError(
            "Chua co league_catalog.json. "
            "Hay chay catalog.py truoc."
        )

    with SOURCE.open("r", encoding="utf-8") as f:
        leagues = json.load(f)

    ranked = []
    queue = []

    for item in leagues:
        score, tier, details = calculate_score(item)

        seasons = item.get("seasons") or []
        sorted_seasons = sorted(
            seasons,
            key=lambda s: safe_year(s.get("year")),
            reverse=True
        )

        current_seasons = [
            s for s in sorted_seasons
            if s.get("current") is True
        ]
        other_seasons = [
            s for s in sorted_seasons
            if s.get("current") is not True
        ]

        ordered_seasons = current_seasons + other_seasons

        ranked_item = {
            "league_id": item.get("league_id"),
            "league_name": item.get("league_name"),
            "country": item.get("country"),
            "league_type": item.get("league_type"),
            "priority_score": score,
            "priority_tier": tier,
            "latest_season": item.get("latest_season"),
            "season_count": len(seasons),
            "coverage_details": details,
        }

        ranked.append(ranked_item)

        for season in ordered_seasons:
            year = safe_year(season.get("year"))
            if not year:
                continue

            queue.append({
                "league_id": item.get("league_id"),
                "league_name": item.get("league_name"),
                "country": item.get("country"),
                "season": year,
                "is_current": season.get("current") is True,
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

    queue.sort(
        key=lambda x: (
            x["priority_score"],
            x["is_current"],
            x["season"]
        ),
        reverse=True
    )

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
            "league_id", "league_name", "country",
            "league_type", "priority_score",
            "priority_tier", "latest_season",
            "season_count"
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for item in ranked:
            writer.writerow({
                key: item.get(key) for key in fields
            })

    tier_counts = {
        tier: sum(1 for x in ranked
                  if x["priority_tier"] == tier)
        for tier in ("A", "B", "C")
    }

    print("===== AI FOOTBALL PRIORITY V2.2 =====")
    print("Leagues ranked:", len(ranked))
    print("Historical season tasks:", len(queue))
    print("Priority groups:", tier_counts)

    print("\nTOP 10 PRIORITY LEAGUES")
    for item in ranked[:10]:
        print(
            item["priority_tier"],
            item["priority_score"],
            item["country"],
            "-",
            item["league_name"]
        )

    print("\nPRIORITY ENGINE COMPLETED")

if __name__ == "__main__":
    main()
