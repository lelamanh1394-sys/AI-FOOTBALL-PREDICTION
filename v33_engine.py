
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# ==========================================================
# AI FOOTBALL PREDICTION V3.3
# ODDS QUALITY + MATCH RESEARCH ENGINE
#
# Reads V3.2 online scanner output.
# Does NOT claim bookmaker odds are independent predictions.
# Does NOT issue betting recommendations without an
# independently validated probability model.
# ==========================================================

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
DATA_DIR = Path("data/online")
OUTPUT_DIR = Path("data/analysis")

MAX_MATCHES = 20
MIN_BOOKMAKERS = 2
MIN_ODD = 1.01
MAX_ODD = 100.0


def now_local():
    return datetime.now(TZ)


def load_latest():
    today = now_local().strftime("%Y-%m-%d")
    path = DATA_DIR / today / "latest.json"

    if not path.exists():
        raise FileNotFoundError(
            f"V3.2 data not found: {path}"
        )

    with path.open("r", encoding="utf-8") as f:
        return json.load(f), path


def valid_odd(value):
    try:
        odd = float(value)
        return MIN_ODD <= odd <= MAX_ODD
    except (TypeError, ValueError):
        return False


def normalize_name(value):
    return str(value or "").strip().lower()


def get_1x2_market(bookmaker):
    """
    Find a standard 1X2 market in API-Football odds.
    Returns Home, Draw, Away odds or None.
    """

    result = {}

    for bet in bookmaker.get("bets", []):
        market_name = normalize_name(bet.get("name"))

        if market_name not in (
            "match winner",
            "1x2",
            "fulltime result",
            "full time result"
        ):
            continue

        for item in bet.get("values", []):
            name = normalize_name(item.get("value"))
            odd = item.get("odd")

            if not valid_odd(odd):
                continue

            if name in ("home", "1"):
                result["home"] = float(odd)
            elif name in ("draw", "x"):
                result["draw"] = float(odd)
            elif name in ("away", "2"):
                result["away"] = float(odd)

    if all(k in result for k in ("home", "draw", "away")):
        return result

    return None


def devig_probabilities(odds):
    """
    Remove the bookmaker margin from one bookmaker's
    1X2 odds. This is a market-implied estimate,
    NOT an independent AI probability.
    """

    inverse = {
        key: 1.0 / value
        for key, value in odds.items()
    }

    total = sum(inverse.values())

    if total <= 0:
        return None

    return {
        key: value / total
        for key, value in inverse.items()
    }


def analyse_match(item, captured_at):
    fixture = item.get("fixture_info") or {}
    bookmakers = item.get("bookmakers") or []

    if not fixture:
        return None

    home = fixture.get("home") or {}
    away = fixture.get("away") or {}
    league = fixture.get("league") or {}

    bookmaker_data = []
    best_odds = {
        "home": 0.0,
        "draw": 0.0,
        "away": 0.0
    }

    for bookmaker in bookmakers:
        market = get_1x2_market(bookmaker)

        if not market:
            continue

        probabilities = devig_probabilities(market)

        if not probabilities:
            continue

        bookmaker_data.append({
            "name": bookmaker.get("name", "Unknown"),
            "odds": market,
            "market_probabilities": probabilities
        })

        for key in best_odds:
            best_odds[key] = max(
                best_odds[key],
                market[key]
            )

    if len(bookmaker_data) < MIN_BOOKMAKERS:
        return None

    consensus = {}

    for key in ("home", "draw", "away"):
        values = [
            book["market_probabilities"][key]
            for book in bookmaker_data
        ]

        consensus[key] = round(
            statistics.mean(values), 4
        )

    dispersion = {}

    for key in ("home", "draw", "away"):
        values = [
            book["market_probabilities"][key]
            for book in bookmaker_data
        ]

        dispersion[key] = round(
            statistics.pstdev(values), 4
        )

    kickoff = fixture.get("date")

    return {
        "fixture_id": fixture.get("fixture_id"),
        "kickoff": kickoff,
        "league": league.get("name"),
        "country": league.get("country"),
        "home_team": home.get("name"),
        "away_team": away.get("name"),
        "bookmaker_count": len(bookmaker_data),
        "bookmakers": bookmaker_data,
        "best_available_odds": best_odds,
        "market_consensus_no_margin": consensus,
        "market_dispersion": dispersion,
        "captured_at": captured_at,
        "model_probability": None,
        "expected_value": None,
        "recommendation": "RESEARCH_ONLY",
        "reason": (
            "Independent team-strength model is not "
            "yet available or validated."
        )
    }


def is_upcoming(item, current_time):
    fixture = item.get("fixture_info") or {}
    status = fixture.get("status") or {}

    if status.get("short") not in ("NS", "TBD"):
        return False

    timestamp = fixture.get("timestamp")

    if timestamp is None:
        return False

    try:
        kickoff = datetime.fromtimestamp(
            int(timestamp), timezone.utc
        )

        return kickoff > current_time.astimezone(timezone.utc)

    except (TypeError, ValueError, OverflowError):
        return False


def main():
    print("=" * 65)
    print("AI FOOTBALL PREDICTION V3.3")
    print("ODDS QUALITY AND MATCH RESEARCH")
    print("=" * 65)

    data, source_path = load_latest()

    captured_at = data.get("finished_at")
    if not captured_at:
        captured_at = data.get("started_at")

    current_time = now_local()

    candidates = []

    for item in data.get("odds", []):
        if not is_upcoming(item, current_time):
            continue

        result = analyse_match(item, captured_at)

        if result:
            candidates.append(result)

    # Prioritize matches with more bookmakers and
    # lower disagreement between market probabilities.
    candidates.sort(
        key=lambda x: (
            -x["bookmaker_count"],
            sum(x["market_dispersion"].values())
        )
    )

    selected = candidates[:MAX_MATCHES]

    report = {
        "engine": "AI Football Prediction V3.3",
        "created_at": current_time.isoformat(),
        "source_file": str(source_path),
        "source_engine": data.get("engine"),
        "purpose": "odds_quality_and_match_research",
        "limits": {
            "maximum_matches": MAX_MATCHES,
            "minimum_bookmakers": MIN_BOOKMAKERS
        },
        "summary": {
            "upcoming_matches_with_odds": len(candidates),
            "selected_for_research": len(selected),
            "independent_model_ready": False,
            "betting_recommendations": 0
        },
        "important_notice": (
            "Market consensus is derived from bookmaker odds. "
            "It is not an independent prediction and must not "
            "be used alone to claim positive expected value."
        ),
        "matches": selected
    }

    date_folder = current_time.strftime("%Y-%m-%d")
    output_dir = OUTPUT_DIR / date_folder
    output_dir.mkdir(parents=True, exist_ok=True)

    output_file = output_dir / "v33_report.json"

    with output_file.open("w", encoding="utf-8") as f:
        json.dump(
            report,
            f,
            ensure_ascii=False,
            indent=2
        )

    print("\n===== V3.3 SUMMARY =====")
    print("Upcoming matches with odds:", len(candidates))
    print("Selected for research:", len(selected))
    print("Independent model ready: NO")
    print("Betting recommendations: 0")
    print("Saved report:", output_file)

    for number, match in enumerate(selected, start=1):
        print(
            f"{number}. {match['home_team']} - "
            f"{match['away_team']} | "
            f"Bookmakers: {match['bookmaker_count']}"
        )

    print("\nV3.3 COMPLETED")


if __name__ == "__main__":
    main()
