import unittest

from wnba_analyzer import db


def seed_game(conn, provider_game_id, date, home="H", away="A", home_score=80, away_score=75):
    game_id = db.upsert_game(
        conn=conn,
        provider="test",
        provider_game_id=provider_game_id,
        game_date=date,
        home_team=home,
        away_team=away,
        status="final",
        period=4,
        home_score=home_score,
        away_score=away_score,
    )

    quarters = [
        (1, 20, 18),
        (2, 20, 19),
        (3, 20, 18),
        (4, 20, 20),
    ]

    for p, h, a in quarters:
        db.upsert_quarter_score(conn, game_id, p, h, a)

    db.upsert_team_stats_for_game(
        conn=conn,
        game_id=game_id,
        home_team=home,
        away_team=away,
        home_score=home_score,
        away_score=away_score,
        game_date=date,
    )

    return game_id


class TestHistory(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_walk_forward_history_excludes_future(self):
        seed_game(self.conn, "g1", "2025-01-01 12:00:00")
        seed_game(self.conn, "g2", "2025-01-02 12:00:00")

        hist_before_g2 = db.get_team_history(
            self.conn,
            team="H",
            before_date="2025-01-02 00:00:00",
        )
        self.assertEqual(len(hist_before_g2), 1)

        hist_after_g2 = db.get_team_history(
            self.conn,
            team="H",
            before_date="2025-01-03 00:00:00",
        )
        self.assertEqual(len(hist_after_g2), 2)

        hist_before_g1 = db.get_team_history(
            self.conn,
            team="H",
            before_date="2025-01-01 00:00:00",
        )
        self.assertEqual(len(hist_before_g1), 0)

    def test_home_away_splits(self):
        seed_game(self.conn, "g1", "2025-01-01 12:00:00", home="H", away="A")

        home_games = db.get_team_history(
            self.conn,
            team="H",
            before_date="2025-01-02 00:00:00",
            home_only=True,
        )
        away_games = db.get_team_history(
            self.conn,
            team="H",
            before_date="2025-01-02 00:00:00",
            home_only=False,
        )

        self.assertEqual(len(home_games), 1)
        self.assertEqual(len(away_games), 0)


if __name__ == "__main__":
    unittest.main()
