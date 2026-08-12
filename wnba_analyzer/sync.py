from . import db


def _upsert_normalized_game(conn, provider_name: str, game: dict) -> int:
    game_id = db.upsert_game(
        conn=conn,
        provider=provider_name,
        provider_game_id=str(game.get("provider_game_id")),
        game_date=str(game.get("game_date")),
        home_team=str(game.get("home_team")),
        away_team=str(game.get("away_team")),
        status=str(game.get("status", "scheduled")),
        period=game.get("period"),
        home_score=game.get("home_score"),
        away_score=game.get("away_score"),
    )

    for q in game.get("quarters", []) or []:
        try:
            p = int(q.get("period"))
        except Exception:
            continue

        if p < 1 or p > 4:
            continue

        hs = q.get("home")
        ascr = q.get("away")

        if hs is None or ascr is None:
            continue

        db.upsert_quarter_score(conn, game_id, p, hs, ascr)

    if (
        game.get("status") == "final"
        and game.get("home_score") is not None
        and game.get("away_score") is not None
    ):
        db.upsert_team_stats_for_game(
            conn=conn,
            game_id=game_id,
            home_team=str(game.get("home_team")),
            away_team=str(game.get("away_team")),
            home_score=game.get("home_score"),
            away_score=game.get("away_score"),
            game_date=str(game.get("game_date")),
        )

    return game_id


def sync_history(conn, provider, config, odds_provider=None) -> int:
    getter = getattr(provider, "get_history_games", None)
    if not callable(getter):
        return 0

    games = getter() or []
    count = 0

    for game in games:
        game_id = _upsert_normalized_game(conn, provider.name, game)

        if odds_provider is not None and getattr(odds_provider, "historical", False):
            snaps = odds_provider.get_snapshots_for_games([game])
            for snap in snaps:
                db.insert_odds_snapshot(
                    conn=conn,
                    game_id=game_id,
                    provider=snap.get("provider", odds_provider.name),
                    market=snap.get("market", "totals"),
                    line=snap.get("line"),
                    over_price=snap.get("over_price"),
                    under_price=snap.get("under_price"),
                )

        count += 1

    conn.commit()
    return count


def sync_scoreboard(conn, provider, config):
    synced = []

    games = provider.get_scoreboard() or []
    for game in games:
        game_id = _upsert_normalized_game(conn, provider.name, game)
        synced.append({"game_id": game_id, "game": game})

    conn.commit()
    return synced


def sync_odds(conn, odds_provider, synced, config) -> int:
    if not config.enable_odds or odds_provider is None:
        return 0

    normalized_games = [item["game"] for item in synced]
    snapshots = odds_provider.get_snapshots_for_games(normalized_games) or []

    id_map = {
        item["game"].get("provider_game_id"): item["game_id"]
        for item in synced
    }

    count = 0
    for snap in snapshots:
        game_id = id_map.get(snap.get("provider_game_id"))
        if not game_id:
            continue

        db.insert_odds_snapshot(
            conn=conn,
            game_id=game_id,
            provider=snap.get("provider", odds_provider.name),
            market=snap.get("market", "totals"),
            line=snap.get("line"),
            over_price=snap.get("over_price"),
            under_price=snap.get("under_price"),
        )
        count += 1

    conn.commit()
    return count
