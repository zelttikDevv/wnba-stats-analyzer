import unittest

from wnba_analyzer import models


class TestProjections(unittest.TestCase):
    def test_naive_q3_formula(self):
        p = models.naive_projection([20, 22, 24], league_avg=150, interval_z=1.28)
        # Q1+Q2+Q3 = 66, promedio = 22, proyección = 88
        self.assertAlmostEqual(p.central, 88.0, places=3)
        self.assertEqual(p.meta["known_quarters"], 3)

    def test_current_projection_uses_known_quarters(self):
        p = models.current_game_projection([20, 30], interval_z=1.28)
        self.assertIsNotNone(p)
        self.assertEqual(p.meta["known_quarters"], 2)
        self.assertEqual(p.meta["known_total"], 50.0)
        self.assertGreater(p.central, 50.0)

    def test_historical_no_history_falls_back_to_league(self):
        p = models.historical_projection(
            home_team="A",
            away_team="B",
            home_hist=[],
            away_hist=[],
            league_avg=154.0,
            min_hist_games=8,
        )
        self.assertAlmostEqual(p.central, 154.0, places=3)
        self.assertEqual(p.meta["home_hist_games"], 0)
        self.assertEqual(p.meta["away_hist_games"], 0)

    def test_combined_without_history_uses_current(self):
        current = models.current_game_projection([20, 22, 24])
        historical = models.historical_projection(
            home_team="A",
            away_team="B",
            home_hist=[],
            away_hist=[],
            league_avg=154.0,
            min_hist_games=8,
        )
        combined = models.combined_projection(
            current=current,
            historical=historical,
            known_quarters=3,
            hist_counts=(0, 0),
            min_hist_games=8,
        )
        self.assertAlmostEqual(combined.central, current.central, places=3)
        self.assertAlmostEqual(combined.meta["weight_current"], 1.0, places=3)

    def test_error_metrics(self):
        met = models.error_metrics([2.0, -2.0])
        self.assertEqual(met["n"], 2)
        self.assertAlmostEqual(met["mae"], 2.0)
        self.assertAlmostEqual(met["rmse"], 2.0)
        self.assertAlmostEqual(met["bias"], 0.0)

    def test_signal_and_roi_helpers(self):
        sig = models.make_signal(central=160.0, std=5.0, line=155.0, threshold=2.0)
        self.assertEqual(sig["signal"], "OVER")
        self.assertAlmostEqual(sig["diff"], 5.0)

        win_profit = models.profit_for_american(-110, stake=1.0)
        self.assertAlmostEqual(win_profit, 100.0 / 110.0, places=5)

        profits = [win_profit, -1.0]
        total = sum(profits)
        self.assertLess(total, 0.0)


if __name__ == "__main__":
    unittest.main()
