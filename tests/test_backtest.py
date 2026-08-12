import unittest

from wnba_analyzer import db, backtest
from wnba_analyzer.config import Config

from tests.test_db_history import seed_game


class TestBacktest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)

        seed_game(self.conn, "g1", "2025-01-01 12:00:00")
        seed_game(self.conn, "g2", "2025-01-02 12:00:00")
        seed_game(self.conn, "g3", "2025-01-03 12:00:00")

        self.cfg = Config(
            db_path=":memory:",
            hist_window=5,
            min_hist_games=1,
            snapshot_quarter=3,
            interval_z=1.28,
        )

    def tearDown(self):
        self.conn.close()

    def test_pure_backtest_runs_and_reports_metrics(self):
        res = backtest.run_pure_backtest(
            conn=self.conn,
            config=self.cfg,
            run_id="test-run",
            save_predictions=False,
        )

        self.assertEqual(res["skipped"], 0)
        self.assertIn("naive", res["metrics"])
        self.assertIn("combined", res["metrics"])
        self.assertGreaterEqual(res["metrics"]["naive"]["n"], 1)

    def test_signals_backtest_disabled_by_default(self):
        cfg = Config(enable_odds=False)
        res = backtest.run_signals_backtest(self.conn, cfg, run_id="test")
        self.assertIn("error", res)


if __name__ == "__main__":
    unittest.main()
