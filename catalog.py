
import json
import csv
from pathlib import Path
from collections import Counter

SOURCE = Path("data/leagues/all_leagues.json")
OUTPUT = Path("data/catalog")

def main():
    if not SOURCE.exists():
        raise FileNotFoundError("Chua co du lieu all_leagues.json")

    with SOURCE.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    records = raw.get("response", [])
    catalogue = []
    countries = Counter()
    types = Counter()
    coverage_counts = Counter()

    for item in records:
        league = item.get("league") or {}
        country = item.get("country") or {}
        seasons = item.get("seasons") or []

        coverage = {}
        if seasons:
            coverage = seasons[-1].get("coverage") or {}

        fixtures = coverage.get("fixtures") or {}

        entry = {
            "league_id": league.get("id"),
            "league_name": league.get("name"),
            "league_type": league.get("type"),
            "country": country.get("name"),
            "country_code": country.get("code"),
            "seasons": [
                {
                    "year": s.get("year"),
                    "start": s.get("start"),
                    "end": s.get("end"),
                    "current": s.get("current")
                }
                for s in seasons
            ],
            "latest_season": seasons[-1].get("year") if seasons else None,
            "coverage": coverage
        }

        catalogue.append(entry)
        countries[entry["country"] or "Unknown"] += 1
        types[entry["league_type"] or "Unknown"] += 1

        for key in [
            "events", "lineups", "statistics_fixtures",
            "statistics", "players"
        ]:
            if fixtures.get(key) is True:
                coverage_counts[key] += 1

        for key in ["standings", "players", "top_scorers",
                    "top_assists", "top_cards"]:
            if coverage.get(key) is True:
                coverage_counts[key] += 1

    OUTPUT.mkdir(parents=True, exist_ok=True)

    with (OUTPUT / "league_catalog.json").open(
        "w", encoding="utf-8"
    ) as f:
        json.dump(catalogue, f, ensure_ascii=False, indent=2)

    with (OUTPUT / "coverage_summary.json").open(
        "w", encoding="utf-8"
    ) as f:
        json.dump({
            "total_leagues": len(catalogue),
            "countries": len(countries),
            "league_types": dict(types),
            "leagues_by_country": dict(countries),
            "coverage_counts": dict(coverage_counts)
        }, f, ensure_ascii=False, indent=2)

    with (OUTPUT / "league_catalog.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "league_id", "league_name", "league_type",
                "country", "country_code", "latest_season"
            ]
        )
        writer.writeheader()
        for row in catalogue:
            writer.writerow({
                key: row.get(key)
                for key in writer.fieldnames
            })

    print("===== AI FOOTBALL CATALOG V2.1 =====")
    print("Total leagues:", len(catalogue))
    print("Countries:", len(countries))
    print("League types:", dict(types))
    print("Coverage:", dict(coverage_counts))
    print("CATALOG COMPLETED")

if __name__ == "__main__":
    main()
