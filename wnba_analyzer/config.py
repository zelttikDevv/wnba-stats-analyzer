import os
from dataclasses import dataclass


def _bool(key: str, default: bool = False) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except Exception:
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except Exception:
        return default


@dataclass
class Config:
    db_path: str = "wnba.db"

    enable_odds: bool = False
    odds_provider: str = "demo"
    odds_api_key: str = ""
    odds_region: str = "us"
    odds_market: str = "totals"
    odds_sport_key: str = "basketball_wnba"

    sports_provider: str = "demo"
    espn_base: str = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba"

    poll_seconds: int = 20
    cache_seconds: int = 15
    http_timeout: int = 10
    max_retries: int = 2

    hist_window: int = 10
    min_hist_games: int = 8
    snapshot_quarter: int = 3

    h2h_weight: float = 0.10
    signal_threshold: float = 2.0
    interval_z: float = 1.28

    data_dir: str = "data"


def load_config() -> Config:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

    return Config(
        db_path=os.getenv("DB_PATH", "wnba.db"),
        enable_odds=_bool("ENABLE_ODDS", False),
        odds_provider=os.getenv("ODDS_PROVIDER", "demo"),
        odds_api_key=os.getenv("ODDS_API_KEY", ""),
        odds_region=os.getenv("ODDS_REGION", "us"),
        odds_market=os.getenv("ODDS_MARKET", "totals"),
        odds_sport_key=os.getenv("ODDS_SPORT_KEY", "basketball_wnba"),
        sports_provider=os.getenv("SPORTS_PROVIDER", "demo"),
        espn_base=os.getenv(
            "ESPN_BASE_URL",
            "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba",
        ),
        poll_seconds=_int("POLL_SECONDS", 20),
        cache_seconds=_int("CACHE_SECONDS", 15),
        http_timeout=_int("HTTP_TIMEOUT", 10),
        max_retries=_int("MAX_RETRIES", 2),
        hist_window=_int("HIST_WINDOW", 10),
        min_hist_games=_int("WNBA_MIN_HIST_GAMES", 8),
        snapshot_quarter=_int("SNAPSHOT_QUARTER", 3),
        h2h_weight=_float("H2H_WEIGHT", 0.10),
        signal_threshold=_float("SIGNAL_THRESHOLD", 2.0),
        interval_z=_float("INTERVAL_Z", 1.28),
        data_dir=os.getenv("DATA_DIR", "data"),
    )
