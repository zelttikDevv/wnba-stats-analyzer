import hashlib

try:
    import requests
except Exception:
    requests = None


class OddsProvider:
    name = "base"
    historical = False

    def __init__(self, config):
        self.config = config

    def get_snapshots_for_games(self, games):
        return []


class NullOddsProvider(OddsProvider):
    name = "null"
    historical = False

    def get_snapshots_for_games(self, games):
        return []


class DemoOddsProvider(OddsProvider):
    """
    Odds ficticias para pruebas locales.

    No intenta representar mercados reales. Sirve para probar
    la capa opcional de señales sin depender de una API externa.
    """

    name = "demo_odds"
    historical = True

    def _line_for_game(self, game: dict) -> float:
        gid = str(game.get("provider_game_id", ""))
        digest = hashlib.sha256(gid.encode("utf-8")).hexdigest()
        return float(148 + (int(digest, 16) % 25))

    def get_snapshots_for_games(self, games):
        out = []
        for g in games:
            line = self._line_for_game(g)
            out.append(
                {
                    "provider_game_id": g.get("provider_game_id"),
                    "provider": self.name,
                    "market": "totals",
                    "line": line,
                    "over_price": -110,
                    "under_price": -110,
                }
            )
        return out


class TheOddsApiProvider(OddsProvider):
    """
    Integración opcional con The Odds API.

    IMPORTANTE:
    - Debes verificar si actualmente existe plan gratuito real.
    - Debes verificar si el deporte 'basketball_wnba' está disponible.
    - Esta capa solo se usa si ENABLE_ODDS=true.
    - No es necesaria para proyectar puntos ni para backtest puro.
    """

    name = "theoddsapi"
    historical = False

    def _norm(self, s: str) -> str:
        return "".join(ch for ch in str(s).lower() if ch.isalnum())

    def _key(self, home: str, away: str) -> str:
        return f"{self._norm(home)}|{self._norm(away)}"

    def _extract_totals(self, event: dict):
        bookmakers = event.get("bookmakers", [])
        for bm in bookmakers:
            markets = bm.get("markets", [])
            for market in markets:
                if market.get("key") != "totals":
                    continue

                line = None
                over_price = None
                under_price = None

                for outcome in market.get("outcomes", []):
                    name = outcome.get("name")
                    if name == "Over":
                        line = outcome.get("point")
                        over_price = outcome.get("price")
                    elif name == "Under":
                        line = outcome.get("point")
                        under_price = outcome.get("price")

                if line is not None:
                    return float(line), over_price, under_price

        return None, None, None

    def get_snapshots_for_games(self, games):
        if not self.config.enable_odds:
            return []
        if not self.config.odds_api_key:
            return []
        if requests is None:
            return []

        url = f"https://api.the-odds-api.com/v4/sports/{self.config.odds_sport_key}/odds/"
        params = {
            "apiKey": self.config.odds_api_key,
            "regions": self.config.odds_region,
            "markets": self.config.odds_market,
            "oddsFormat": "american",
            "dateFormat": "iso",
        }

        try:
            resp = requests.get(url, params=params, timeout=self.config.http_timeout)
            data = resp.json()
        except Exception:
            return []

        if not isinstance(data, list):
            return []

        local_by_key = {}
        for g in games:
            key = self._key(g.get("home_team", ""), g.get("away_team", ""))
            local_by_key[key] = g.get("provider_game_id")

        out = []

        for ev in data:
            home = ev.get("home_team", "")
            away = ev.get("away_team", "")

            provider_game_id = local_by_key.get(self._key(home, away))
            if not provider_game_id:
                provider_game_id = local_by_key.get(self._key(away, home))

            if not provider_game_id:
                continue

            line, over_price, under_price = self._extract_totals(ev)
            if line is None:
                continue

            out.append(
                {
                    "provider_game_id": provider_game_id,
                    "provider": self.name,
                    "market": "totals",
                    "line": line,
                    "over_price": over_price,
                    "under_price": under_price,
                }
            )

        return out


def get_odds_provider(config) -> OddsProvider:
    if not config.enable_odds:
        return NullOddsProvider(config)

    name = (config.odds_provider or "demo").lower()

    if name == "demo":
        return DemoOddsProvider(config)
    if name == "theoddsapi":
        return TheOddsApiProvider(config)

    raise ValueError(f"ODDS_PROVIDER desconocido: {config.odds_provider}")
