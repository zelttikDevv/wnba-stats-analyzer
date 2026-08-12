import json
import sqlite3
from datetime import datetime, timezone


SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    provider_game_id TEXT NOT NULL,
    game_date TEXT NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    status TEXT NOT NULL,
    period INTEGER,
    home_score INTEGER,
    away_score INTEGER,
    final_total INTEGER,
    last_updated TEXT,
    UNIQUE(provider, provider_game_id)
);

CREATE TABLE IF NOT EXISTS quarter_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER NOT NULL REFERENCES games(id),
    period INTEGER NOT NULL,
    home_score INTEGER,
    away_score INTEGER,
    UNIQUE(game_id, period)
);

CREATE TABLE IF NOT EXISTS team_game_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER NOT NULL REFERENCES games(id),
    team TEXT NOT NULL,
    opponent TEXT NOT NULL,
    home INTEGER NOT NULL,
    game_date TEXT NOT NULL,
    points_for INTEGER,
    points_against INTEGER,
    q1_for INTEGER,
    q1_against INTEGER,
    q2_for INTEGER,
    q2_against INTEGER,
    q3_for INTEGER,
    q3_against INTEGER,
    q4_for INTEGER,
    q4_against INTEGER,
    UNIQUE(game_id, team)
);

CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    game_id INTEGER NOT NULL REFERENCES games(id),
    model TEXT NOT NULL,
    snapshot_quarter INTEGER,
    central REAL,
    lower REAL,
    upper REAL,
    std REAL,
    diff_vs_line REAL,
    signal TEXT,
    confidence REAL,
    meta TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS odds_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER NOT NULL REFERENCES games(id),
    provider TEXT NOT NULL,
    market TEXT NOT NULL DEFAULT 'totals',
    line REAL,
    over_price REAL,
    under_price REAL,
    snapshot_at TEXT
);

CREATE TABLE IF NOT EXISTS backtest_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    backtest_type TEXT NOT NULL,
    model TEXT NOT NULL,
    metrics_json TEXT,
    created_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_team_game_stats_team_date
ON team_game_stats(team, game_date);

CREATE INDEX IF NOT EXISTS idx_games_date
ON games(game_date);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def upsert_game(
    conn: sqlite3.Connection,
    provider: str,
    provider_game_id: str,
    game_date: str,
    home_team: str,
    away_team: str,
    status: str,
    period=None,
    home_score=None,
    away_score=None,
) -> int:
    final_total = None
    if status == "final" and home_score is not None and away_score is not None:
        final_total = int(home_score) + int(away_score)

    conn.execute(
        """
        INSERT INTO games(
            provider, provider_game_id, game_date, home_team, away_team,
            status, period, home_score, away_score, final_total, last_updated
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(provider, provider_game_id) DO UPDATE SET
            game_date=excluded.game_date,
            home_team=excluded.home_team,
            away_team=excluded.away_team,
            status=excluded.status,
            period=excluded.period,
            home_score=excluded.home_score,
            away_score=excluded.away_score,
            final_total=excluded.final_total,
            last_updated=excluded.last_updated
        """,
        (
            provider,
            provider_game_id,
            game_date,
            home_team,
            away_team,
            status,
            period,
            home_score,
            away_score,
            final_total,
            now_iso(),
        ),
    )

    row = conn.execute(
        "SELECT id FROM games WHERE provider=? AND provider_game_id=?",
        (provider, provider_game_id),
    ).fetchone()
    conn.commit()
    return int(row["id"])


def upsert_quarter_score(
    conn: sqlite3.Connection,
    game_id: int,
    period: int,
    home_score,
    away_score,
) -> None:
    conn.execute(
        """
        INSERT INTO quarter_scores(game_id, period, home_score, away_score)
        VALUES(?,?,?,?)
        ON CONFLICT(game_id, period) DO UPDATE SET
            home_score=excluded.home_score,
            away_score=excluded.away_score
        """,
        (game_id, int(period), home_score, away_score),
    )


def _q(values, idx):
    try:
        return values[idx]
    except Exception:
        return None


def _upsert_team_game_stats(
    conn: sqlite3.Connection,
    game_id: int,
    team: str,
    opponent: str,
    home: bool,
    game_date: str,
    points_for,
    points_against,
    q_for,
    q_against,
) -> None:
    conn.execute(
        """
        INSERT INTO team_game_stats(
            game_id, team, opponent, home, game_date, points_for, points_against,
            q1_for, q1_against, q2_for, q2_against, q3_for, q3_against,
            q4_for, q4_against
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(game_id, team) DO UPDATE SET
            opponent=excluded.opponent,
            home=excluded.home,
            game_date=excluded.game_date,
            points_for=excluded.points_for,
            points_against=excluded.points_against,
            q1_for=excluded.q1_for,
            q1_against=excluded.q1_against,
            q2_for=excluded.q2_for,
            q2_against=excluded.q2_against,
            q3_for=excluded.q3_for,
            q3_against=excluded.q3_against,
            q4_for=excluded.q4_for,
            q4_against=excluded.q4_against
        """,
        (
            game_id,
            team,
            opponent,
            1 if home else 0,
            game_date,
            points_for,
            points_against,
            _q(q_for, 0),
            _q(q_against, 0),
            _q(q_for, 1),
            _q(q_against, 1),
            _q(q_for, 2),
            _q(q_against, 2),
            _q(q_for, 3),
            _q(q_against, 3),
        ),
    )


def upsert_team_stats_for_game(
    conn: sqlite3.Connection,
    game_id: int,
    home_team: str,
    away_team: str,
    home_score,
    away_score,
    game_date: str,
) -> None:
    if home_score is None or away_score is None:
        return

    rows = conn.execute(
        """
        SELECT period, home_score, away_score
        FROM quarter_scores
        WHERE game_id = ?
        ORDER BY period
        """,
        (game_id,),
    ).fetchall()

    home_q = [None, None, None, None]
    away_q = [None, None, None, None]

    for r in rows:
        p = int(r["period"])
        if 1 <= p <= 4:
            home_q[p - 1] = r["home_score"]
            away_q[p - 1] = r["away_score"]

    _upsert_team_game_stats(
        conn,
        game_id=game_id,
        team=home_team,
        opponent=away_team,
        home=True,
        game_date=game_date,
        points_for=home_score,
        points_against=away_score,
        q_for=home_q,
        q_against=away_q,
    )

    _upsert_team_game_stats(
        conn,
        game_id=game_id,
        team=away_team,
        opponent=home_team,
        home=False,
        game_date=game_date,
        points_for=away_score,
        points_against=home_score,
        q_for=away_q,
        q_against=home_q,
    )


def get_game(conn: sqlite3.Connection, game_id: int):
    row = conn.execute("SELECT * FROM games WHERE id=?", (game_id,)).fetchone()
    return dict(row) if row else None


def get_team_history(
    conn: sqlite3.Connection,
    team: str,
    before_date: str | None = None,
    exclude_game_id: int | None = None,
    limit: int = 10,
    home_only: bool | None = None,
):
    sql = """
        SELECT *
        FROM team_game_stats
        WHERE team = ?
          AND points_for IS NOT NULL
          AND points_against IS NOT NULL
    """
    params = [team]

    if before_date:
        sql += " AND game_date < ?"
        params.append(before_date)

    if exclude_game_id is not None:
        sql += " AND game_id != ?"
        params.append(exclude_game_id)

    if home_only is True:
        sql += " AND home = 1"
    elif home_only is False:
        sql += " AND home = 0"

    sql += " ORDER BY game_date DESC, game_id DESC LIMIT ?"
    params.append(int(limit))

    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_league_average_total(
    conn: sqlite3.Connection,
    before_date: str | None = None,
    default: float = 154.0,
) -> float:
    sql = "SELECT AVG(final_total) AS avg_total FROM games WHERE final_total IS NOT NULL"
    params = []

    if before_date:
        sql += " AND game_date < ?"
        params.append(before_date)

    row = conn.execute(sql, params).fetchone()
    if not row or row["avg_total"] is None:
        return float(default)
    return float(row["avg_total"])


def get_h2h_totals(
    conn: sqlite3.Connection,
    team_a: str,
    team_b: str,
    before_date: str | None = None,
    limit: int = 8,
):
    sql = """
        SELECT final_total
        FROM games
        WHERE final_total IS NOT NULL
          AND (
            (home_team = ? AND away_team = ?)
            OR
            (home_team = ? AND away_team = ?)
          )
    """
    params = [team_a, team_b, team_b, team_a]

    if before_date:
        sql += " AND game_date < ?"
        params.append(before_date)

    sql += " ORDER BY game_date DESC LIMIT ?"
    params.append(int(limit))

    rows = conn.execute(sql, params).fetchall()
    return [float(r["final_total"]) for r in rows if r["final_total"] is not None]


def get_latest_odds_line(
    conn: sqlite3.Connection,
    game_id: int,
    market: str = "totals",
):
    row = conn.execute(
        """
        SELECT line
        FROM odds_snapshots
        WHERE game_id = ? AND market = ?
        ORDER BY snapshot_at DESC, id DESC
        LIMIT 1
        """,
        (game_id, market),
    ).fetchone()
    if not row or row["line"] is None:
        return None
    return float(row["line"])


def insert_prediction(
    conn: sqlite3.Connection,
    run_id: str | None,
    game_id: int,
    model: str,
    snapshot_quarter: int | None,
    central: float,
    lower: float,
    upper: float,
    std: float,
    diff_vs_line: float | None = None,
    signal: str | None = None,
    confidence: float | None = None,
    meta: dict | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO predictions(
            run_id, game_id, model, snapshot_quarter, central, lower, upper,
            std, diff_vs_line, signal, confidence, meta, created_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            run_id,
            game_id,
            model,
            snapshot_quarter,
            float(central),
            float(lower),
            float(upper),
            float(std),
            diff_vs_line,
            signal,
            confidence,
            json.dumps(meta or {}, ensure_ascii=False),
            now_iso(),
        ),
    )


def insert_odds_snapshot(
    conn: sqlite3.Connection,
    game_id: int,
    provider: str,
    market: str,
    line: float | None,
    over_price: float | None,
    under_price: float | None,
) -> None:
    conn.execute(
        """
        INSERT INTO odds_snapshots(
            game_id, provider, market, line, over_price, under_price, snapshot_at
        )
        VALUES(?,?,?,?,?,?,?)
        """,
        (game_id, provider, market, line, over_price, under_price, now_iso()),
    )


def insert_backtest_result(
    conn: sqlite3.Connection,
    run_id: str,
    backtest_type: str,
    model: str,
    metrics: dict,
) -> None:
    conn.execute(
        """
        INSERT INTO backtest_results(run_id, backtest_type, model, metrics_json, created_at)
        VALUES(?,?,?,?,?)
        """,
        (
            run_id,
            backtest_type,
            model,
            json.dumps(metrics, ensure_ascii=False),
            now_iso(),
        ),
)
