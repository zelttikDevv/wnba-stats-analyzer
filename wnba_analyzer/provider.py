import json
import os
import random
import time
from datetime import datetime, timedelta, timezone

try:
    import requests
except Exception:
    requests = None


class ProviderError(Exception):
    pass


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class SportsProvider:
    name = "base"

    def __init__(self, config):
        self.config = config
        self._cache = {}
        self._cache_ts = {}

    def get_scoreboard(self):
        return []

    def get_history_games(self):
        return []

    def _get_cached(self, key: str):
        ttl = getattr(self.config, "cache_seconds", 0)
        ts = self._cache_ts.get(key)
        if ttl and ts and (time.time() - ts) < ttl:
            return self._cache.get(key)
        return None

    def _set_cached(self, key: str, value):
        self._cache[key] = value
        self._cache_ts[key] = time.time()


class DemoProvider(SportsProvider):
    """
    Proveedor ficticio, 100% offline.

    Genera histórico suficiente para backtest y un partido en vivo
    guionado para demostrar la proyección en Q3/Q4.
    """

    name = "demo"
    TEAMS = [
        "Demo Liberty",
        "Demo Aces",
        "Demo Sun",
        "Demo Sky",
        "Dreamers",
        "Mystics",
        "Lynx",
        "Mercury",
    ]

    def __init__(self, config):
        super().__init__(config)
        self._tick = 0
        self._history = self._build_history(96)

    def _build_history(self, n: int):
        now = datetime.now(timezone.utc)
        strengths = {team: random.Random(team).gauss(0.0, 1.2) for team in self.TEAMS}
        games = []

        for i in range(n):
            home = self.TEAMS[i % len(self.TEAMS)]
            away = self.TEAMS[(i + 3) % len(self.TEAMS)]
            if home == away:
                away = self.TEAMS[(i + 4) % len(self.TEAMS)]

            date = now - timedelta(days=1 + i // 4, hours=i % 6)
            rnd = random.Random(f"{home}|{away}|{date.isoformat()}")

            hq = []
            aq = []
            for _ in range(4):
                hq.append(max(12, int(round(rnd.gauss(19.5 + strengths[home], 3.0)))))
                aq.append(max(12, int(round(rnd.gauss(19.5 + strengths[away], 3.0)))))

            games.append(
                {
                    "provider_game_id": f"demo-hist-{i}",
                    "game_date": date.strftime("%Y-%m-%d %H:%M:%S"),
                    "home_team": home,
                    "away_team": away,
                    "status": "final",
                    "period": 4,
                    "home_score": sum(hq),
                    "away_score": sum(aq),
                    "quarters": [
                        {"period": p + 1, "home": hq[p], "away": aq[p]}
                        for p in range(4)
                    ],
                }
            )

        return games

    def get_history_games(self):
        return self._history

    def get_scoreboard(self):
        self._tick += 1
        home = self.TEAMS[0]
        away = self.TEAMS[1]

        hq = [21, 22, 20, 19]
        aq = [19, 21, 18, 20]
        tick = self._tick

        if tick <= 1:
            status = "live"
            period = 3
            completed = 3
            home_score = sum(hq[:3])
            away_score = sum(aq[:3])
        elif tick == 2:
            status = "live"
            period = 4
            completed = 3
            frac = 0.55
            home_score = sum(hq[:3]) + int(round(hq[3] * frac))
            away_score = sum(aq[:3]) + int(round(aq[3] * frac))
        else:
            status = "final"
            period = 4
            completed = 4
            home_score = sum(hq)
            away_score = sum(aq)

        quarters = [
            {"period": i + 1, "home": hq[i], "away": aq[i]}
            for i in range(completed)
        ]

        return [
            {
                "provider_game_id": "demo-live-1",
                "game_date": _now_str(),
                "home_team": home,
                "away_team": away,
                "status": status,
                "period": period,
                "home_score": home_score,
                "away_score": away_score,
                "quarters": quarters,
            }
        ]


class EspnProvider(SportsProvider):
    """
    Proveedor ESPN no oficial.

    ADVERTENCIA:
    - No es una API oficial WNBA.
    - Puede cambiar sin aviso.
    - Puede no entregar cuartos completos.
    - Puede no entregar histórico.
    - Debe validarse para tu región/fecha.
    """

    name = "espn"

    def __init__(self, config):
        super().__init__(config)
        self.base = (config.espn_base or "").rstrip("/") or (
            "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba"
        )

    def _get_json(self, url: str):
        if requests is None:
            raise ProviderError("requests no está instalado")

        cached = self._get_cached(url)
        if cached is not None:
            return cached

        last_err = None
        for attempt in range(max(1, self.config.max_retries)):
            try:
                resp = requests.get(
                    url,
                    timeout=self.config.http_timeout,
                    headers={"User-Agent": "wnba-analyzer/0.1"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    self._set_cached(url, data)
                    return data

                if resp.status_code in (429, 500, 502, 503, 504):
                    time.sleep(1.0 + attempt)
                    continue

                raise ProviderError(f"HTTP {resp.status_code} para {url}")
            except requests.RequestException as exc:
                last_err = exc
                time.sleep(1.0 + attempt)

        raise ProviderError(f"No se pudo obtener {url}: {last_err}")

    def _parse_date(self, s: str | None) -> str:
        if not s:
            return _now_str()
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return _now_str()

    def _parse_score(self, competitor: dict):
        s = competitor.get("score")
        if isinstance(s, dict):
            s = s.get("value")
        try:
            return int(float(s))
        except Exception:
            return None

    def _parse_linescores(self, competitor: dict):
        raw = competitor.get("linescores") or []
        out = []
        for item in raw:
            if isinstance(item, dict):
                value = item.get("value", item.get("score"))
            else:
                value = item
            try:
                out.append(int(float(value)))
            except Exception:
                out.append(None)
        return out

    def _parse_event(self, ev: dict):
        competitions = ev.get("competitions") or [{}]
        comp = competitions[0]
        competitors = comp.get("competitors") or []

        home_comp = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away_comp = next((c for c in competitors if c.get("homeAway") == "away"), None)

        if not home_comp or not away_comp:
            return None

        status_type = ev.get("status", {}).get("type", {})
        completed = bool(status_type.get("completed", False))
        state = status_type.get("state", "pre")

        if completed:
            status = "final"
        elif state == "in":
            status = "live"
        else:
            status = "scheduled"

        period = ev.get("status", {}).get("period") or comp.get("period")
        try:
            period = int(period)
        except Exception:
            period = None

        home_team = (
            home_comp.get("team", {}).get("displayName")
            or home_comp.get("team", {}).get("name")
            or "HOME"
        )
        away_team = (
            away_comp.get("team", {}).get("displayName")
            or away_comp.get("team", {}).get("name")
            or "AWAY"
        )

        home_score = self._parse_score(home_comp)
        away_score = self._parse_score(away_comp)

        home_vals = self._parse_linescores(home_comp)
        away_vals = self._parse_linescores(away_comp)

        if status == "final":
            completed_periods = period if period else min(len(home_vals), len(away_vals))
        elif status == "live":
            completed_periods = max(0, (period or 1) - 1)
        else:
            completed_periods = 0

        quarters = []
        for i in range(min(completed_periods, len(home_vals), len(away_vals))):
            hv = home_vals[i]
            av = away_vals[i]
            if hv is not None and av is not None:
                quarters.append({"period": i + 1, "home": hv, "away": av})

        return {
            "provider_game_id": str(ev.get("id")),
            "game_date": self._parse_date(ev.get("date")),
            "home_team": home_team,
            "away_team": away_team,
            "status": status,
            "period": period,
            "home_score": home_score,
            "away_score": away_score,
            "quarters": quarters,
        }

    def get_scoreboard(self):
        url = f"{self.base}/scoreboard"
        data = self._get_json(url)
        events = data.get("events", [])
        games = []

        for ev in events:
            try:
                g = self._parse_event(ev)
                if g:
                    games.append(g)
            except Exception:
                continue

        return games


class FileProvider(SportsProvider):
    """
    Proveedor basado en archivos JSON locales.

    data/scoreboard.json -> lista de partidos normalizados.
    data/history.json    -> lista de partidos históricos normalizados.

    Formato normalizado por partido:
    {
      "provider_game_id": "...",
      "game_date": "YYYY-MM-DD HH:MM:SS",
      "home_team": "...",
      "away_team": "...",
      "status": "scheduled|live|final",
      "period": 3,
      "home_score": 60,
      "away_score": 58,
      "quarters": [
        {"period": 1, "home": 20, "away": 18},
        {"period": 2, "home": 22, "away": 21}
      ]
    }
    """

    name = "file"

    def _read(self, filename: str):
        path = os.path.join(self.config.data_dir, filename)
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception:
            return []

    def get_scoreboard(self):
        return self._read("scoreboard.json")

    def get_history_games(self):
        return self._read("history.json")


def get_sports_provider(config) -> SportsProvider:
    name = (config.sports_provider or "demo").lower()

    if name == "demo":
        return DemoProvider(config)
    if name == "espn":
        return EspnProvider(config)
    if name == "file":
        return FileProvider(config)

    raise ProviderError(f"SPORTS_PROVIDER desconocido: {config.sports_provider}")
