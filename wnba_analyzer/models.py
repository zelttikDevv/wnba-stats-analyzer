import math
from dataclasses import dataclass, field


@dataclass
class Projection:
    model: str
    central: float
    std: float
    lower: float
    upper: float
    meta: dict = field(default_factory=dict)


def _mean(values, default=0.0):
    values = list(values)
    if not values:
        return float(default)
    return sum(values) / len(values)


def _sample_std(values, default=0.0):
    values = list(values)
    if len(values) < 2:
        return float(default)
    m = _mean(values)
    var = sum((x - m) ** 2 for x in values) / (len(values) - 1)
    return math.sqrt(max(0.0, var))


def _make_projection(model: str, central: float, std: float, interval_z: float, meta: dict):
    std = max(float(std), 0.01)
    central = float(central)
    return Projection(
        model=model,
        central=central,
        std=std,
        lower=central - interval_z * std,
        upper=central + interval_z * std,
        meta=meta or {},
    )


def naive_projection(
    quarter_totals,
    league_avg: float = 154.0,
    interval_z: float = 1.28,
) -> Projection:
    quarter_totals = [float(x) for x in quarter_totals if x is not None]
    n = len(quarter_totals)

    if n == 0:
        central = float(league_avg)
        std = 10.0
        meta = {"known_quarters": 0, "note": "sin cuartos, usa promedio de liga"}
        return _make_projection("naive", central, std, interval_z, meta)

    known = sum(quarter_totals)
    avg = known / n
    central = known if n >= 4 else known + avg * (4 - n)

    q_std = _sample_std(quarter_totals, default=7.0)
    std = max(6.0, q_std * math.sqrt(max(1, 4 - n)))

    if n >= 4:
        std = 1.0

    meta = {
        "known_quarters": n,
        "known_total": known,
        "avg_quarter": avg,
        "pace_proxy_points_per_quarter": avg,
    }
    return _make_projection("naive", central, std, interval_z, meta)


def _slope(values):
    n = len(values)
    if n < 2:
        return 0.0
    mx = (n - 1) / 2.0
    my = sum(values) / n
    denom = sum((i - mx) ** 2 for i in range(n))
    if denom == 0:
        return 0.0
    num = sum((i - mx) * (values[i] - my) for i in range(n))
    return num / denom


def current_game_projection(
    quarter_totals,
    interval_z: float = 1.28,
    damping: float = 0.35,
    max_rate_change: float = 6.0,
):
    quarter_totals = [float(x) for x in quarter_totals if x is not None]
    n = len(quarter_totals)
    if n == 0:
        return None

    known = sum(quarter_totals)
    remaining = max(0, 4 - n)

    weights = [1.0 + i for i in range(n)]
    base_rate = sum(w * x for w, x in zip(weights, quarter_totals)) / sum(weights)

    slope = _slope(quarter_totals) if n >= 2 else 0.0
    projected_rate = base_rate + damping * slope

    low = base_rate - max_rate_change
    high = base_rate + max_rate_change
    projected_rate = min(max(projected_rate, low), high)
    projected_rate = max(0.0, projected_rate)

    central = known if remaining == 0 else known + remaining * projected_rate

    q_std = _sample_std(quarter_totals, default=8.0)
    if remaining == 0:
        std = 1.0
    else:
        std = max(6.5, q_std * math.sqrt(max(1, remaining + 1)))

    meta = {
        "known_quarters": n,
        "known_total": known,
        "remaining_quarters": remaining,
        "weighted_rate": base_rate,
        "slope": slope,
        "projected_rate": projected_rate,
        "pace_proxy_points_per_quarter": base_rate,
        "trend": "accelerating" if slope > 0.5 else ("decelerating" if slope < -0.5 else "stable"),
    }
    return _make_projection("current", central, std, interval_z, meta)


def historical_projection(
    home_team: str,
    away_team: str,
    home_hist,
    away_hist,
    league_avg: float = 154.0,
    min_hist_games: int = 8,
    h2h_totals=None,
    h2h_weight: float = 0.10,
    interval_z: float = 1.28,
) -> Projection:
    home_n = len(home_hist)
    away_n = len(away_hist)
    min_n = min(home_n, away_n)

    half_league = float(league_avg) / 2.0

    home_off = _mean([r["points_for"] for r in home_hist], half_league)
    home_def = _mean([r["points_against"] for r in home_hist], half_league)
    away_off = _mean([r["points_for"] for r in away_hist], half_league)
    away_def = _mean([r["points_against"] for r in away_hist], half_league)

    expected_home = (home_off + away_def) / 2.0
    expected_away = (away_off + home_def) / 2.0
    raw_total = expected_home + expected_away

    if min_hist_games > 0:
        shrink = min(1.0, min_n / float(min_hist_games))
    else:
        shrink = 1.0

    central = shrink * raw_total + (1.0 - shrink) * float(league_avg)

    if h2h_totals and len(h2h_totals) >= 3 and h2h_weight > 0:
        h2h_avg = _mean(h2h_totals)
        diff = h2h_avg - central
        cap = 4.0
        central += max(-cap, min(cap, h2h_weight * diff))

    totals = []
    for r in home_hist + away_hist:
        pf = r.get("points_for")
        pa = r.get("points_against")
        if pf is not None and pa is not None:
            totals.append(float(pf) + float(pa))

    std = max(7.0, _sample_std(totals, default=10.0))

    if min_hist_games > 0 and min_n < min_hist_games:
        std *= 1.0 + (min_hist_games - min_n) / float(min_hist_games)

    meta = {
        "home_team": home_team,
        "away_team": away_team,
        "home_hist_games": home_n,
        "away_hist_games": away_n,
        "min_hist_games": min_n,
        "required_hist_games": min_hist_games,
        "shrinkage_to_league": 1.0 - shrink,
        "expected_home": expected_home,
        "expected_away": expected_away,
        "h2h_games": len(h2h_totals or []),
    }

    return _make_projection("historical", central, std, interval_z, meta)


def combined_projection(
    current: Projection | None,
    historical: Projection | None,
    known_quarters: int,
    hist_counts,
    min_hist_games: int,
    interval_z: float = 1.28,
) -> Projection | None:
    if current is None and historical is None:
        return None

    if current is None:
        return _make_projection(
            "combined",
            historical.central,
            historical.std * 1.02,
            interval_z,
            {
                "weight_current": 0.0,
                "weight_historical": 1.0,
                "known_quarters": known_quarters,
                "note": "sin partido actual suficiente, usa histórico/liga",
            },
        )

    if historical is None:
        return _make_projection(
            "combined",
            current.central,
            current.std * 1.02,
            interval_z,
            {
                "weight_current": 1.0,
                "weight_historical": 0.0,
                "known_quarters": known_quarters,
                "note": "sin histórico, usa partido actual",
            },
        )

    time_weights = {0: 0.0, 1: 0.25, 2: 0.45, 3: 0.70, 4: 0.90}
    known_quarters = max(0, min(4, int(known_quarters)))
    w_time = time_weights.get(known_quarters, 0.90)

    home_hist_n, away_hist_n = hist_counts
    min_hist_n = min(home_hist_n, away_hist_n)

    if min_hist_games > 0:
        hist_conf = min(1.0, min_hist_n / float(min_hist_games))
    else:
        hist_conf = 1.0

    # Si falta histórico, se sube el peso del partido actual.
    w_current = w_time + (1.0 - w_time) * (1.0 - hist_conf)
    w_current = min(1.0, max(0.0, w_current))
    w_hist = 1.0 - w_current

    central = w_current * current.central + w_hist * historical.central
    diff = current.central - historical.central

    var = (
        (w_current ** 2) * (current.std ** 2)
        + (w_hist ** 2) * (historical.std ** 2)
        + w_current * w_hist * (diff ** 2)
    )
    std = math.sqrt(max(var, 1e-6))

    meta = {
        "weight_current": w_current,
        "weight_historical": w_hist,
        "time_weight": w_time,
        "hist_confidence": hist_conf,
        "known_quarters": known_quarters,
        "home_hist_games": home_hist_n,
        "away_hist_games": away_hist_n,
        "required_hist_games": min_hist_games,
        "current_central": current.central,
        "historical_central": historical.central,
    }

    return _make_projection("combined", central, std, interval_z, meta)


def error_metrics(errors):
    errors = list(errors)
    n = len(errors)
    if n == 0:
        return {"n": 0, "mae": None, "rmse": None, "bias": None}

    mae = sum(abs(e) for e in errors) / n
    rmse = math.sqrt(sum(e * e for e in errors) / n)
    bias = sum(errors) / n

    return {
        "n": n,
        "mae": float(mae),
        "rmse": float(rmse),
        "bias": float(bias),
    }


def make_signal(
    central: float,
    std: float,
    line: float,
    threshold: float = 2.0,
):
    diff = float(central) - float(line)
    if abs(diff) < max(0.0, float(threshold)):
        signal = "NO_BET"
    elif diff > 0:
        signal = "OVER"
    else:
        signal = "UNDER"

    confidence = abs(diff) / max(float(std), 1e-6)

    if confidence < 0.20:
        bucket = "low"
    elif confidence < 0.50:
        bucket = "medium"
    else:
        bucket = "high"

    return {
        "diff": float(diff),
        "signal": signal,
        "confidence": float(confidence),
        "confidence_bucket": bucket,
    }


def profit_for_american(price: float, stake: float = 1.0) -> float:
    if price is None:
        return 0.0
    price = float(price)
    if price == 0:
        return 0.0
    if price > 0:
        return stake * price / 100.0
    return stake * 100.0 / abs(price)
