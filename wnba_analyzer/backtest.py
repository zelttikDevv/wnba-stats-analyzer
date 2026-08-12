from collections import defaultdict

from . import db
from .analysis import compute_projections, get_quarter_totals
from .models import error_metrics, make_signal, profit_for_american


MODEL_ORDER = ["naive", "current", "historical", "combined"]


def run_pure_backtest(
    conn,
    config,
    run_id: str | None = None,
    save_predictions: bool = True,
):
    run_id = run_id or db.now_iso()

    games = conn.execute(
        """
        SELECT *
        FROM games
        WHERE status = 'final' AND final_total IS NOT NULL
        ORDER BY game_date ASC, id ASC
        """
    ).fetchall()

    model_errors = {}
    team_errors = {}
    rows = []
    skipped = 0

    for grow in games:
        g = dict(grow)

        totals = get_quarter_totals(
            conn,
            g["id"],
            max_period=config.snapshot_quarter,
        )

        if len(totals) < config.snapshot_quarter:
            skipped += 1
            continue

        result = compute_projections(
            conn=conn,
            game=g,
            config=config,
            quarter_totals=totals,
        )

        actual = float(g["final_total"])

        for proj in result["projections"]:
            err = float(proj.central) - actual

            model_errors.setdefault(proj.model, []).append(err)
            team_errors.setdefault(proj.model, defaultdict(list))

            for team in (g["home_team"], g["away_team"]):
                team_errors[proj.model][team].append(err)

            rows.append(
                {
                    "game_id": g["id"],
                    "model": proj.model,
                    "central": float(proj.central),
                    "std": float(proj.std),
                    "actual": actual,
                    "error": err,
                }
            )

            if save_predictions:
                meta = dict(proj.meta)
                meta["actual_final"] = actual
                meta["backtest"] = "pure"
                db.insert_prediction(
                    conn=conn,
                    run_id=run_id,
                    game_id=g["id"],
                    model=proj.model,
                    snapshot_quarter=config.snapshot_quarter,
                    central=proj.central,
                    lower=proj.lower,
                    upper=proj.upper,
                    std=proj.std,
                    diff_vs_line=None,
                    signal=None,
                    confidence=None,
                    meta=meta,
                )

    metrics = {m: error_metrics(v) for m, v in model_errors.items()}
    team_metrics = {
        m: {team: error_metrics(vals) for team, vals in teams.items()}
        for m, teams in team_errors.items()
    }

    for model in MODEL_ORDER:
        if model in metrics:
            db.insert_backtest_result(
                conn=conn,
                run_id=run_id,
                backtest_type="pure",
                model=model,
                metrics={
                    "model": model,
                    "metrics": metrics[model],
                    "team_metrics": team_metrics.get(model, {}),
                    "snapshot_quarter": config.snapshot_quarter,
                    "hist_window": config.hist_window,
                    "min_hist_games": config.min_hist_games,
                },
            )

    conn.commit()

    return {
        "run_id": run_id,
        "metrics": metrics,
        "team_metrics": team_metrics,
        "rows": rows,
        "skipped": skipped,
    }


def _summary(profits):
    profits = list(profits)
    n = len(profits)

    if n == 0:
        return {
            "n": 0,
            "hit_rate": None,
            "roi": None,
            "total_profit": 0.0,
            "max_drawdown": 0.0,
        }

    total_profit = sum(profits)
    non_push = [p for p in profits if p != 0]
    wins = len([p for p in non_push if p > 0])
    hit_rate = wins / len(non_push) if non_push else None
    roi = total_profit / n if n else None

    cum = 0.0
    peak = 0.0
    max_dd = 0.0

    for p in profits:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    return {
        "n": n,
        "hit_rate": hit_rate,
        "roi": roi,
        "total_profit": total_profit,
        "max_drawdown": max_dd,
    }


def _diff_bucket(diff_abs: float) -> str:
    if diff_abs < 2.0:
        return "<2"
    if diff_abs < 4.0:
        return "2-4"
    if diff_abs < 6.0:
        return "4-6"
    return "6+"


def run_signals_backtest(conn, config, run_id: str | None = None):
    if not config.enable_odds:
        return {
            "error": "ENABLE_ODDS=false. El backtest de señales está desactivado."
        }

    odds_count = conn.execute(
        "SELECT COUNT(*) AS n FROM odds_snapshots"
    ).fetchone()["n"]

    if odds_count == 0:
        return {
            "error": "No hay odds_snapshots en la base de datos. "
                     "Activa ENABLE_ODDS=true y guarda líneas primero."
        }

    run_id = run_id or db.now_iso()

    pure = run_pure_backtest(
        conn=conn,
        config=config,
        run_id=run_id,
        save_predictions=False,
    )

    profits = []
    by_signal = defaultdict(list)
    by_conf = defaultdict(list)
    by_diff = defaultdict(list)
    by_model = defaultdict(list)

    for row in pure["rows"]:
        line = db.get_latest_odds_line(conn, row["game_id"])
        if line is None:
            continue

        sig = make_signal(
            central=row["central"],
            std=row["std"],
            line=line,
            threshold=config.signal_threshold,
        )

        if sig["signal"] == "NO_BET":
            continue

        actual = row["actual"]

        if actual == line:
            profit = 0.0
        else:
            hit = (
                (sig["signal"] == "OVER" and actual > line)
                or
                (sig["signal"] == "UNDER" and actual < line)
            )
            # Si no hay precio guardado, asumimos -110 como referencia hipotética.
            profit = profit_for_american(-110) if hit else -1.0

        profits.append(profit)
        by_signal[sig["signal"]].append(profit)
        by_conf[sig["confidence_bucket"]].append(profit)
        by_diff[_diff_bucket(abs(sig["diff"]))].append(profit)
        by_model[row["model"]].append(profit)

    metrics = {
        "overall": _summary(profits),
        "by_signal": {k: _summary(v) for k, v in by_signal.items()},
        "by_confidence": {k: _summary(v) for k, v in by_conf.items()},
        "by_diff": {k: _summary(v) for k, v in by_diff.items()},
        "by_model": {k: _summary(v) for k, v in by_model.items()},
        "assumed_default_price": -110,
        "snapshot_quarter": config.snapshot_quarter,
        "signal_threshold": config.signal_threshold,
    }

    db.insert_backtest_result(
        conn=conn,
        run_id=run_id,
        backtest_type="signals",
        model="all",
        metrics=metrics,
    )
    conn.commit()

    return metrics
