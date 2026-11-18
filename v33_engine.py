
import json
import statistics

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# ==========================================================
# AI FOOTBALL PREDICTION V3.3.1
# FIX DATA PATH + DATA STRUCTURE + DIAGNOSTICS
# ==========================================================

TZ = ZoneInfo("Asia/Ho_Chi_Minh")

# IMPORTANT: Read the new V3.3B.1 collector output
DATA_DIR = Path("data/public_odds")
OUTPUT_DIR = Path("data/analysis")

MAX_MATCHES = 20
MIN_BOOKMAKERS = 2

MIN_ODD = 1.01
MAX_ODD = 100.0


def now_local():
    return datetime.now(TZ)


# ==========================================================
# 1. LOAD LATEST DATA
# ==========================================================

def load_latest():
    today = now_local().strftime("%Y-%m-%d")

    path = DATA_DIR / today / "latest.json"

    print("\n[1] LOADING ODDS DATA")
    print("Input file:", path)

    if not path.exists():
        raise FileNotFoundError(
            f"Odds data not found: {path}"
        )

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    print("Source engine:", data.get("engine"))
    print("Source:", data.get("source"))
    print("Date:", data.get("date"))

    summary = data.get("summary", {})

    print(
        "Collector fixtures:",
        summary.get("fixtures_received", "Unknown")
    )

    print(
        "Collector raw odds:",
        summary.get("raw_odds_records", "Unknown")
    )

    print(
        "Collector linked matches:",
        summary.get("linked_upcoming_matches", "Unknown")
    )

    return data, path


# ==========================================================
# 2. VALIDATE ODDS
# ==========================================================

def valid_odd(value):
    try:
        odd = float(value)

        return MIN_ODD <= odd <= MAX_ODD

    except (TypeError, ValueError):
        return False


def normalize_name(value):
    return str(value or "").strip().lower()


# ==========================================================
# 3. EXTRACT TEAM NAME
# Supports both string and dictionary formats
# ==========================================================

def get_team_name(value):
    if isinstance(value, dict):
        return value.get("name", "Unknown")

    if isinstance(value, str):
        return value

    return "Unknown"


# ==========================================================
# 4. EXTRACT 1X2 MARKET
# ==========================================================

def get_1x2_market(bookmaker):
    result = {}

    bets = bookmaker.get("bets", [])

    for bet in bets:

        market_name = normalize_name(
            bet.get("name")
        )

        if market_name not in (
            "match winner",
            "1x2",
            "fulltime result",
            "full time result"
        ):
            continue

        for item in bet.get("values", []):

            name = normalize_name(
                item.get("value")
            )

            odd = item.get("odd")

            if not valid_odd(odd):
                continue

            if name in ("home", "1"):
                result["home"] = float(odd)

            elif name in ("draw", "x"):
                result["draw"] = float(odd)

            elif name in ("away", "2"):
                result["away"] = float(odd)

    if all(
        key in result
        for key in ("home", "draw", "away")
    ):
        return result

    return None


# ==========================================================
# 5. REMOVE BOOKMAKER MARGIN
# ==========================================================

def devig_probabilities(odds):

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


# ==========================================================
# 6. CHECK UPCOMING MATCH
# ==========================================================

def is_upcoming(item, current_time):

    fixture = item.get("fixture_info") or {}

    status = fixture.get("status") or {}

    if status.get("short") not in ("NS", "TBD"):
        return False, "INVALID_STATUS"

    timestamp = fixture.get("timestamp")

    if timestamp is None:
        return False, "MISSING_TIMESTAMP"

    try:
        kickoff = datetime.fromtimestamp(
            int(timestamp),
            timezone.utc
        )

        if kickoff <= current_time.astimezone(
            timezone.utc
        ):
            return False, "KICKOFF_PASSED"

    except (
        TypeError,
        ValueError,
        OverflowError
    ):
        return False, "INVALID_TIMESTAMP"

    return True, "OK"


# ==========================================================
# 7. ANALYSE MATCH
# ==========================================================

def analyse_match(item, captured_at, diagnostics):

    fixture = item.get("fixture_info") or {}

    bookmakers = item.get("bookmakers") or []

    if not fixture:
        diagnostics["missing_fixture_info"] += 1
        return None

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

        probabilities = devig_probabilities(
            market
        )

        if not probabilities:
            continue

        bookmaker_data.append({
            "name": bookmaker.get(
                "name", "Unknown"
            ),
            "odds": market,
            "market_probabilities": probabilities
        })

        for key in best_odds:

            best_odds[key] = max(
                best_odds[key],
                market[key]
            )

    if len(bookmaker_data) == 0:
        diagnostics["no_valid_1x2_market"] += 1
        return None

    if len(bookmaker_data) < MIN_BOOKMAKERS:
        diagnostics["below_minimum_bookmakers"] += 1
        return None

    # Market consensus
    consensus = {}

    dispersion = {}

    for key in ("home", "draw", "away"):

        values = [
            book["market_probabilities"][key]
            for book in bookmaker_data
        ]

        consensus[key] = round(
            statistics.mean(values),
            4
        )

        dispersion[key] = round(
            statistics.pstdev(values),
            4
        )

    league = fixture.get("league") or {}

    home_team = get_team_name(
        fixture.get("home")
    )

    away_team = get_team_name(
        fixture.get("away")
    )

    diagnostics["qualified_matches"] += 1

    return {
        "fixture_id": fixture.get("fixture_id"),
        "kickoff": fixture.get("date"),

        "league": league.get("name"),
        "country": league.get("country"),

        "home_team": home_team,
        "away_team": away_team,

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
            "Independent probability model has not "
            "yet been validated."
        )
    }


# ==========================================================
# 8. MAIN ENGINE
# ==========================================================

def main():

    print("=" * 65)
    print("AI FOOTBALL PREDICTION V3.3.1")
    print("ODDS QUALITY + MATCH RESEARCH")
    print("=" * 65)

    data, source_path = load_latest()

    current_time = now_local()

    captured_at = (
        data.get("finished_at")
        or data.get("started_at")
    )

    odds_items = data.get("odds", [])

    diagnostics = {
        "total_odds_records": len(odds_items),
        "not_upcoming": 0,
        "invalid_status": 0,
        "missing_timestamp": 0,
        "kickoff_passed": 0,
        "invalid_timestamp": 0,
        "missing_fixture_info": 0,
        "no_valid_1x2_market": 0,
        "below_minimum_bookmakers": 0,
        "qualified_matches": 0
    }

    print("\n[2] CHECKING UPCOMING MATCHES")

    candidates = []

    for item in odds_items:

        upcoming, reason = is_upcoming(
            item,
            current_time
        )

        if not upcoming:

            diagnostics["not_upcoming"] += 1

            if reason == "INVALID_STATUS":
                diagnostics["invalid_status"] += 1

            elif reason == "MISSING_TIMESTAMP":
                diagnostics["missing_timestamp"] += 1

            elif reason == "KICKOFF_PASSED":
                diagnostics["kickoff_passed"] += 1

            elif reason == "INVALID_TIMESTAMP":
                diagnostics["invalid_timestamp"] += 1

            continue

        result = analyse_match(
            item,
            captured_at,
            diagnostics
        )

        if result:
            candidates.append(result)

    # Prioritize by bookmaker coverage and lower
    # disagreement between market probabilities.

    candidates.sort(
        key=lambda item: (
            -item["bookmaker_count"],
            sum(
                item["market_dispersion"].values()
            )
        )
    )

    selected = candidates[:MAX_MATCHES]

    # ======================================================
    # 9. BUILD REPORT
    # ======================================================

    report = {
        "engine": "AI Football Prediction V3.3.1",

        "created_at": current_time.isoformat(),

        "source_file": str(source_path),

        "source_engine": data.get("engine"),

        "source": data.get("source"),

        "purpose": "odds_quality_and_match_research",

        "limits": {
            "maximum_matches": MAX_MATCHES,
            "minimum_bookmakers": MIN_BOOKMAKERS
        },

        "summary": {
            "total_odds_records": len(odds_items),

            "upcoming_matches_with_odds": len(candidates),

            "selected_for_research": len(selected),

            "independent_model_ready": False,

            "betting_recommendations": 0
        },

        "diagnostics": diagnostics,

        "important_notice": (
            "Bookmaker market consensus is not an "
            "independent AI prediction. No positive "
            "expected value is claimed."
        ),

        "matches": selected
    }

    # ======================================================
    # 10. SAVE REPORT
    # ======================================================

    date_folder = current_time.strftime(
        "%Y-%m-%d"
    )

    output_dir = OUTPUT_DIR / date_folder

    output_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    output_file = (
        output_dir / "v33_report.json"
    )

    with output_file.open(
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            report,
            file,
            ensure_ascii=False,
            indent=2
        )

    # ======================================================
    # 11. PRINT RESULTS
    # ======================================================

    print("\n===== V3.3.1 SUMMARY =====")

    print(
        "Total odds records:",
        len(odds_items)
    )

    print(
        "Upcoming matches with odds:",
        len(candidates)
    )

    print(
        "Selected for research:",
        len(selected)
    )

    print("Independent model ready: NO")

    print("Betting recommendations: 0")

    print("\n===== DIAGNOSTICS =====")

    for key, value in diagnostics.items():

        print(
            key + ":",
            value
        )

    print("\n===== SELECTED MATCHES =====")

    for number, match in enumerate(
        selected,
        start=1
    ):

        print(
            f"{number}. "
            f"{match['home_team']} - "
            f"{match['away_team']} | "
            f"Bookmakers: "
            f"{match['bookmaker_count']}"
        )

    print("\nSaved report:", output_file)

    print("\nV3.3.1 COMPLETED")


if __name__ == "__main__":
    main()
