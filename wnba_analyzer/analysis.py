from datetime import datetime

from . import db
from .models import (
    naive_projection,
    current_game_projection,
    historical_projection,
    combined_projection,
    make_signal,
)


def _parse_dt(s: str | None):
    if not s:
        return None

    s = str(s).strip()

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            pass

    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _is_back_to_back(last_date: str | None, current_date: str | None) -> bool:
    d1 = _parse_dt(last_date)
    d2 = _parse_dt(current_date)
    if not d1 or not d2:
        return False
    return abs((d2 - d1).days) <= 1


def get_quarter_totals(conn, game_id: int, max_period: int | None = None):
    rows = conn.execute(
        """
        SELECT period, home_score, away_score
        FROM quarter_scores
        WHERE game_id = ?
        ORDER BY period
        """,
        (game_id,),
    ).fetchall()

    by_period = {}

    for r in rows:
        p = int(r["period"])
        if p < 1 or p > 4:
            continue
        if r["home_score"] is None or r["away_score"] is None:
            continue
        by_period[p] = int(r["home_score"]) + int(r["away_score"])

    max_p = 4 if max_period is None else min(4, int(max_period))

    totals = []
    for p in range(1, max_p + 1):
        if p in by_period:
            totals.append(by_period[p])
        else:
            break

    return totals


def compute_projections(conn, game: dict, config, quarter_totals=None):
    if quarter_totals is None:
        quarter_totals = get_quarter_totals(conn, game["id"])

    known_quarters = len(quarter_totals)
    before_date = game.get("game_date")
    exclude_game_id = game.get("id")

    home_all = db.get_team_history(
        conn,
        team=game["home_team"],
        before_date=before_date,
        exclude_game_id=exclude_game_id,
        limit=config.hist_window,
    )

    away_all = db.get_team_history(
        conn,
        team=game["away_team"],
        before_date=before_date,
        exclude_game_id=exclude_game_id,
        limit=config.hist_window,
    )

    # Splits local/visitante si hay muestra suficiente.
    home_home = db.get_team_history(
        conn,
        team=game["home_team"],
        before_date=before_date,
        exclude_game_id=exclude_game_id,
        limit=config.hist_window,
        home_only=True,
    )

    away_away = db.get_team_history(
        conn,
        team=game["away_team"],
        before_date=before_date,
        exclude_game_id=exclude_game_id,
        limit=config.hist_window,
        home_only=False,
    )

    home_hist = home_home if len(home_home) >= 3 else home_all
    away_hist = away_away if len(away_away) >= 3 else away_all

    league_avg = db.get_league_average_total(conn, before_date=before_date)

    h2h_totals = db.get_h2h_totals(
        conn,
        team_a=game["home_team"],
        team_b=game["away_team"],
        before_date=before_date,
        limit=8,
    )

    naive = naive_projection(
        quarter_totals=quarter_totals,
        league_avg=league_avg,
        interval_z=config.interval_z,
    )

    current = current_game_projection(
        quarter_totals=quarter_totals,
        interval_z=config.interval_z,
    )

    historical = historical_projection(
        home_team=game["home_team"],
        away_team=game["away_team"],
        home_hist=home_hist,
        away_hist=away_hist,
        league_avg=league_avg,
        min_hist_games=config.min_hist_games,
        h2h_totals=h2h_totals,
        h2h_weight=config.h2h_weight,
        interval_z=config.interval_z,
    )

    home_last = home_all[0]["game_date"] if home_all else None
    away_last = away_all[0]["game_date"] if away_all else None

    b2b_home = _is_back_to_back(home_last, before_date)
    b2b_away = _is_back_to_back(away_last, before_date)

    if b2b_home or b2b_away:
        historical.std *= 1.10
        historical.lower = historical.central - config.interval_z * historical.std
        historical.upper = historical.central + config.interval_z * historical.std
        historical.meta["back_to_back"] = True
        historical.meta["back_to_back_home"] = b2b_home
        historical.meta["back_to_back_away"] = b2b_away

    combined = combined_projection(
        current=current,
        historical=historical,
        known_quarters=known_quarters,
        hist_counts=(len(home_hist), len(away_hist)),
        min_hist_games=config.min_hist_games,
        interval_z=config.interval_z,
    )

    projections = [naive]
    if current is not None:
        projections.append(current)
    projections.append(historical)
    if combined is not None:
        projections.append(combined)

    hist_counts = {
        "home": len(home_hist),
        "away": len(away_hist),
        "min": min(len(home_hist), len(away_hist)),
        "threshold": config.min_hist_games,
        "ok": min(len(home_hist), len(away_hist)) >= config.min_hist_games,
    }

    return {
        "game": game,
        "quarter_totals": quarter_totals,
        "known_quarters": known_quarters,
        "league_avg": league_avg,
        "hist_counts": hist_counts,
        "projections": projections,
    }


def analyze_game(conn, game: dict, config, run_id: str | None = None, save_predictions: bool = True):
    result = compute_projections(conn, game, config)

    quarter_rows = conn.execute(
        """
        SELECT period, home_score, away_score
        FROM quarter_scores
        WHERE game_id = ?
        ORDER BY period
        """,
        (game["id"],),
    ).fetchall()
    result["quarter_rows"] = [dict(r) for r in quarter_rows]

    line = None
    if config.enable_odds:
        line = db.get_latest_odds_line(conn, game["id"])

    enriched = []

    for proj in result["projections"]:
        diff = None
        signal = None
        confidence = None

        if line is not None:
            sig = make_signal(
                central=proj.central,
                std=proj.std,
                line=line,
                threshold=config.signal_threshold,
            )
            diff = sig["diff"]
            signal = sig["signal"]
            confidence = sig["confidence"]

        if save_predictions:
            meta = dict(proj.meta)
            meta["actual_final"] = game.get("final_total")
            db.insert_prediction(
                conn=conn,
                run_id=run_id,
                game_id=game["id"],
                model=proj.model,
                snapshot_quarter=result["known_quarters"],
                central=proj.central,
                lower=proj.lower,
                upper=proj.upper,
                std=proj.std,
                diff_vs_line=diff,
                signal=signal,
                confidence=confidence,
                meta=meta,
            )

        enriched.append(
            {
                "projection": proj,
                "diff": diff,
                "signal": signal,
                "confidence": confidence,
            }
        )

    conn.commit()

    result["line"] = line
    result["enriched"] = enriched

    return result
