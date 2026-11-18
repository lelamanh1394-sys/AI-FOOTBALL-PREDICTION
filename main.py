
import math
from datetime import datetime

VERSION = "1.0"

# =====================================
# AI FOOTBALL ENGINE V1
# Statistical prediction engine
# =====================================

MAX_GOALS = 10


def poisson_probability(goals, expected_goals):
    return (
        math.exp(-expected_goals)
        * expected_goals ** goals
        / math.factorial(goals)
    )


def analyze_match(match):
    home = match["home"]
    away = match["away"]

    xg_home = float(match["xg_home"])
    xg_away = float(match["xg_away"])

    if xg_home < 0 or xg_away < 0:
        raise ValueError("Expected goals cannot be negative")

    # Goal probability distributions
    home_probs = [
        poisson_probability(i, xg_home)
        for i in range(MAX_GOALS + 1)
    ]

    away_probs = [
        poisson_probability(i, xg_away)
        for i in range(MAX_GOALS + 1)
    ]

    # Score matrix
    p_home = 0.0
    p_draw = 0.0
    p_away = 0.0
    p_over25 = 0.0
    p_btts = 0.0

    for h in range(MAX_GOALS + 1):
        for a in range(MAX_GOALS + 1):
            p = home_probs[h] * away_probs[a]

            if h > a:
                p_home += p
            elif h == a:
                p_draw += p
            else:
                p_away += p

            if h + a >= 3:
                p_over25 += p

            if h >= 1 and a >= 1:
                p_btts += p

    p_under25 = 1 - p_over25
    p_btts_no = 1 - p_btts

    # Expected value per unit stake
    odds = match.get("odds", {})

    probabilities = {
        "HOME": p_home,
        "DRAW": p_draw,
        "AWAY": p_away,
        "OVER25": p_over25,
        "UNDER25": p_under25,
        "BTTS_YES": p_btts,
        "BTTS_NO": p_btts_no
    }

    values = []

    for market, probability in probabilities.items():
        odd = odds.get(market)

        if odd is None:
            continue

        odd = float(odd)

        if odd <= 1:
            continue

        ev = probability * odd - 1

        values.append({
            "market": market,
            "probability": probability,
            "odds": odd,
            "ev": ev,
            "break_even": 1 / odd
        })

    values.sort(key=lambda x: x["ev"], reverse=True)

    return {
        "home": home,
        "away": away,
        "xg_home": xg_home,
        "xg_away": xg_away,
        "home_win": p_home,
        "draw": p_draw,
        "away_win": p_away,
        "over25": p_over25,
        "under25": p_under25,
        "btts": p_btts,
        "markets": values
    }


def percent(value):
    return f"{value * 100:.2f}%"


def main():
    print("=" * 55)
    print("AI FOOTBALL ENGINE V1")
    print("Statistical Prediction System")
    print("=" * 55)

    # DEMO DATA ONLY - NOT REAL MATCHES
    matches = [
        {
            "home": "DEMO TEAM A",
            "away": "DEMO TEAM B",
            "xg_home": 1.65,
            "xg_away": 1.05,
            "odds": {
                "HOME": 1.90,
                "DRAW": 3.40,
                "AWAY": 4.20,
                "OVER25": 1.95,
                "UNDER25": 1.85
            }
        },
        {
            "home": "DEMO TEAM C",
            "away": "DEMO TEAM D",
            "xg_home": 1.20,
            "xg_away": 1.30,
            "odds": {
                "HOME": 2.70,
                "DRAW": 3.10,
                "AWAY": 2.65,
                "OVER25": 2.00,
                "UNDER25": 1.80
            }
        }
    ]

    results = []

    for match in matches:
        result = analyze_match(match)
        results.append(result)

        print(f"\n{result['home']} vs {result['away']}")
        print("-" * 45)

        print("Expected goals:")
        print(f"Home: {result['xg_home']:.2f}")
        print(f"Away: {result['xg_away']:.2f}")

        print("\n1X2 probabilities:")
        print("Home win:", percent(result["home_win"]))
        print("Draw:", percent(result["draw"]))
        print("Away win:", percent(result["away_win"]))

        print("\nGoals:")
        print("Over 2.5:", percent(result["over25"]))
        print("Under 2.5:", percent(result["under25"]))
        print("Both teams score:", percent(result["btts"]))

        print("\nExpected value:")
        for item in result["markets"]:
            print(
                item["market"],
                "| Probability:", percent(item["probability"]),
                "| Odds:", item["odds"],
                "| EV:", percent(item["ev"])
            )

    # Rank positive EV candidates
    candidates = []

    for result in results:
        for market in result["markets"]:
            if market["ev"] > 0:
                candidates.append({
                    "match": result["home"] + " vs " + result["away"],
                    **market
                })

    candidates.sort(key=lambda x: x["ev"], reverse=True)

    print("\n" + "=" * 55)
    print("TOP VALUE CANDIDATES - DEMO")
    print("=" * 55)

    if not candidates:
        print("No positive EV candidates.")
    else:
        for i, item in enumerate(candidates[:10], 1):
            print(
                f"{i}. {item['match']} | {item['market']} "
                f"| P={percent(item['probability'])} "
                f"| Odds={item['odds']} "
                f"| EV={percent(item['ev'])}"
            )

    print("\nNOTE: DEMO DATA ONLY.")
    print("Positive EV is a model estimate, not guaranteed profit.")
    print("Version:", VERSION)
    print("Completed:", datetime.now().isoformat())


if __name__ == "__main__":
    main()
