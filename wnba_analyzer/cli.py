import argparse
import time
from dataclasses import replace

from . import db
from .config import load_config
from .provider import get_sports_provider, DemoProvider
from .odds import get_odds_provider
from .sync import sync_history, sync_scoreboard, sync_odds
from .analysis import analyze_game
from .backtest import run_pure_backtest, run_signals_backtest, MODEL_ORDER


DISCLAIMER = (
    "AVISO: esto es análisis probabilístico. No es una apuesta segura, "
    "no garantiza ganancias y el rendimiento histórico no garantiza resultados futuros."
)


def fmt(x, nd=1):
    if x is None:
        return "-"
    try:
        return f"{float(x):.{nd}f}"
    except Exception:
        return "-"


def print_game_report(game: dict, result: dict, cfg):
    print("=" * 78)
    print("WNBA STATS ANALYZER")
    print(DISCLAIMER)
    print("-" * 78)

    print(
        f"Partido: {game.get('away_team')} @ {game.get('home_team')} | "
        f"Estado: {game.get('status')} | Periodo: {game.get('period')}"
    )
    print(f"Fecha UTC: {game.get('game_date')}")
    print(
        f"Marcador: {fmt(game.get('away_score'), 0)} - {fmt(game.get('home_score'), 0)} "
        f"(away-home)"
    )

    qrows = result.get("quarter_rows", [])
    if qrows:
        parts = []
        for r in qrows:
            parts.append(
                f"Q{r['period']}: {fmt(r['away_score'], 0)}-{fmt(r['home_score'], 0)}"
            )
        print("Cuartos: " + " | ".join(parts))
    else:
        print("Cuartos: sin datos de cuartos disponibles")

    hist = result.get("hist_counts", {})
    print(
        f"Histórico: {game.get('home_team')} {hist.get('home', 0)} partidos, "
        f"{game.get('away_team')} {hist.get('away', 0)} partidos "
        f"(mínimo recomendado: {hist.get('threshold', cfg.min_hist_games)})"
    )

    if not hist.get("ok", False):
        print(
            "AVISO: histórico por debajo del mínimo recomendado. "
            "La proyección se degrada a modelos más simples."
        )

    line = result.get("line")
    if line is not None:
        print(f"Línea Over/Under: {fmt(line, 1)}")
    elif cfg.enable_odds:
        print("Línea Over/Under: no disponible")
    else:
        print("Línea Over/Under: desactivada (ENABLE_ODDS=false)")

    print("-" * 78)
    print(
        f"{'Modelo':12} {'Central':>8} {'Rango':>17} {'Std':>6} "
        f"{'Línea':>6} {'Diff':>6} {'Señal':>8} {'Conf':>6}"
    )

    for item in result.get("enriched", []):
        p = item["projection"]
        range_str = f"{fmt(p.lower, 1)}-{fmt(p.upper, 1)}"
        print(
            f"{p.model:12} {fmt(p.central, 1):>8} {range_str:>17} {fmt(p.std, 1):>6} "
            f"{fmt(line, 1):>6} {fmt(item.get('diff'), 1):>6} "
            f"{item.get('signal') or '-':>8} {fmt(item.get('confidence'), 2):>6}"
        )

    for item in result.get("enriched", []):
        p = item["projection"]
        if p.model == "combined":
            wcur = p.meta.get("weight_current")
            whist = p.meta.get("weight_historical")
            if wcur is not None and whist is not None:
                print(
                    f"Combinado: peso partido actual={fmt(wcur, 2)}, "
                    f"peso histórico={fmt(whist, 2)}"
                )

    print("=" * 78)
    print()


def print_pure_report(res: dict):
    print("=" * 78)
    print("BACKTEST PURO (sin odds)")
    print("Métrica de error contra total real del partido.")
    print(DISCLAIMER)
    print("-" * 78)
    print(f"Partidos saltados por faltar cuartos: {res.get('skipped', 0)}")
    print()
    print(f"{'Modelo':12} {'N':>5} {'MAE':>8} {'RMSE':>8} {'Sesgo':>8}")

    for model in MODEL_ORDER:
        met = res.get("metrics", {}).get(model)
        if not met:
            continue
        print(
            f"{model:12} {met.get('n', 0):>5} "
            f"{fmt(met.get('mae'), 2):>8} "
            f"{fmt(met.get('rmse'), 2):>8} "
            f"{fmt(met.get('bias'), 2):>8}"
        )

    print()
    print("Sesgo positivo = sobreestima el total real.")
    print("Sesgo negativo = subestima el total real.")

    team_metrics = res.get("team_metrics", {}).get("combined", {})
    if team_metrics:
        valid = [
            (team, met)
            for team, met in team_metrics.items()
            if met.get("mae") is not None
        ]
        valid.sort(key=lambda x: x[1]["mae"])

        if valid:
            print()
            print("Equipo con mejor MAE en combined:")
            team, met = valid[0]
            print(f"  {team}: MAE={fmt(met['mae'], 2)}, n={met.get('n', 0)}")

            print("Equipo con peor MAE en combined:")
            team, met = valid[-1]
            print(f"  {team}: MAE={fmt(met['mae'], 2)}, n={met.get('n', 0)}")

    print("=" * 78)
    print()


def print_signal_report(res: dict):
    if "error" in res:
        print("=" * 78)
        print("BACKTEST DE SEÑALES")
        print(res["error"])
        print("=" * 78)
        return

    print("=" * 78)
    print("BACKTEST DE SEÑALES (opcional, solo con odds guardadas)")
    print(DISCLAIMER)
    print("-" * 78)

    overall = res.get("overall", {})
    print(
        f"N señales: {overall.get('n', 0)} | "
        f"Hit rate: {fmt((overall.get('hit_rate') or 0) * 100, 1)}% | "
        f"ROI: {fmt((overall.get('roi') or 0) * 100, 1)}% | "
        f"Beneficio hipotético: {fmt(overall.get('total_profit'), 2)} | "
        f"Max drawdown: {fmt(overall.get('max_drawdown'), 2)}"
    )

    print()
    print("Por modelo:")
    for model, met in res.get("by_model", {}).items():
        print(
            f"  {model:12} n={met.get('n', 0):3d} "
            f"hit={fmt((met.get('hit_rate') or 0) * 100, 1)}% "
            f"roi={fmt((met.get('roi') or 0) * 100, 1)}% "
            f"profit={fmt(met.get('total_profit'), 2)}"
        )

    print()
    print("Por señal:")
    for sig, met in res.get("by_signal", {}).items():
        print(
            f"  {sig:6} n={met.get('n', 0):3d} "
            f"hit={fmt((met.get('hit_rate') or 0) * 100, 1)}% "
            f"profit={fmt(met.get('total_profit'), 2)}"
        )

    print()
    print("Por confianza:")
    for conf, met in res.get("by_confidence", {}).items():
        print(
            f"  {conf:8} n={met.get('n', 0):3d} "
            f"hit={fmt((met.get('hit_rate') or 0) * 100, 1)}% "
            f"profit={fmt(met.get('total_profit'), 2)}"
        )

    print()
    print("Por magnitud diff vs línea:")
    for bucket, met in res.get("by_diff", {}).items():
        print(
            f"  {bucket:6} n={met.get('n', 0):3d} "
            f"hit={fmt((met.get('hit_rate') or 0) * 100, 1)}% "
            f"profit={fmt(met.get('total_profit'), 2)}"
        )

    print()
    print(
        "Nota: si no había precio guardado, se asumió -110 como precio hipotético."
    )
    print("=" * 78)
    print()


def cmd_backtest(cfg, signals: bool):
    conn = db.connect(cfg.db_path)
    db.init_db(conn)

    count = conn.execute(
        "SELECT COUNT(*) AS n FROM games WHERE final_total IS NOT NULL"
    ).fetchone()["n"]

    if count == 0:
        print("Base de datos vacía. Cargando datos DEMO para backtest puro.")
        demo = DemoProvider(cfg)
        odds_provider = get_odds_provider(cfg) if cfg.enable_odds else None
        sync_history(conn, demo, cfg, odds_provider)

    if signals:
        res = run_signals_backtest(conn, cfg)
        if isinstance(res, dict) and "error" in res:
            print_signal_report(res)
            return 1
        print_signal_report(res)
        return 0

    res = run_pure_backtest(conn, cfg)
    print_pure_report(res)
    return 0


def cmd_watch(cfg, watch: bool, game_id_filter: str | None):
    conn = db.connect(cfg.db_path)
    db.init_db(conn)

    provider = get_sports_provider(cfg)
    odds_provider = get_odds_provider(cfg) if cfg.enable_odds else None

    if cfg.sports_provider == "demo":
        print("SPORTS_PROVIDER=demo: usando datos ficticios, sin red.")
    elif cfg.sports_provider == "espn":
        print(
            "SPORTS_PROVIDER=espn: usando proveedor ESPN NO OFICIAL. "
            "Puede fallar o no entregar cuartos/histórico."
        )

    sync_history(conn, provider, cfg, odds_provider)

    try:
        while True:
            try:
                synced = sync_scoreboard(conn, provider, cfg)

                if cfg.enable_odds and odds_provider is not None:
                    sync_odds(conn, odds_provider, synced, cfg)

                if not synced:
                    print("No hay partidos devueltos por el proveedor.")

                for item in synced:
                    game = db.get_game(conn, item["game_id"])
                    if not game:
                        continue

                    if game_id_filter and str(game.get("provider_game_id")) != game_id_filter:
                        continue

                    result = analyze_game(
                        conn=conn,
                        game=game,
                        config=cfg,
                        run_id="live",
                        save_predictions=True,
                    )
                    print_game_report(game, result, cfg)

                print(f"Última actualización: {db.now_iso()} UTC")

            except Exception as exc:
                print(f"Error actualizando: {exc}")
                if not watch:
                    return 1

            if not watch:
                break

            print(f"Esperando {cfg.poll_seconds} segundos... Ctrl-C para salir.")
            time.sleep(cfg.poll_seconds)

    except KeyboardInterrupt:
        print("\nDetenido por el usuario.")
        return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="WNBA Stats Analyzer: análisis y proyección probabilística de puntos."
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--demo", action="store_true", help="partido ficticio, sin red")
    mode.add_argument("--live", action="store_true", help="partidos reales según SPORTS_PROVIDER")
    mode.add_argument("--backtest", action="store_true", help="backtest puro MAE/RMSE/sesgo")

    parser.add_argument(
        "--signals",
        action="store_true",
        help="solo para --backtest: backtest de señales si ENABLE_ODDS=true",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="actualizar continuamente en modo demo/live",
    )
    parser.add_argument(
        "--game-id",
        default=None,
        help="filtrar por provider_game_id",
    )

    args = parser.parse_args(argv)
    cfg = load_config()

    if args.demo:
        cfg.sports_provider = "demo"

    if args.backtest:
        return cmd_backtest(cfg, signals=args.signals)

    if args.demo:
        return cmd_watch(cfg, watch=args.watch, game_id_filter=args.game_id)

    if args.live:
        if cfg.sports_provider == "demo":
            print(
                "ATENCIÓN: --live con SPORTS_PROVIDER=demo usa datos ficticios. "
                "Para intentar datos reales configura SPORTS_PROVIDER=espn o file."
            )
        return cmd_watch(cfg, watch=args.watch, game_id_filter=args.game_id)

    parser.print_help()
    return 0
