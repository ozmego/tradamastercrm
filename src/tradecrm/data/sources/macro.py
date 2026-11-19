"""Makro olay takvimi (Oneri 11d). Planli olaylar onceden bilinir; bu
yuzden "olaya kalan sure" ozelligi lookahead degildir."""

import csv
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from tradecrm.data.provider import timeframe_to_timedelta

DEFAULT_EVENTS_PATH = Path(__file__).resolve().parent.parent / "macro_events.csv"
_ET = ZoneInfo("America/New_York")


def load_events(path: Path | str = DEFAULT_EVENTS_PATH, kinds: set[str] | None = None) -> pd.DatetimeIndex:
    times = []
    with open(path, encoding="utf-8") as f:
        rows = [line for line in f if line.strip() and not line.lstrip().startswith("#")]
    for row in csv.DictReader(rows):
        if kinds and row["event"] not in kinds:
            continue
        local = datetime.strptime(f"{row['date']} {row['time_et']}", "%Y-%m-%d %H:%M").replace(tzinfo=_ET)
        times.append(local.astimezone(UTC))
    return pd.DatetimeIndex(sorted(times))


def event_distance_columns(df: pd.DataFrame, timeframe: str, events: pd.DatetimeIndex) -> pd.DataFrame:
    """Her bar KAPANISI icin: sonraki olaya kalan ve son olaydan gecen saat."""
    close = df.index + timeframe_to_timedelta(timeframe)
    ev = events.as_unit("ns").asi8 if len(events) else np.array([], dtype=np.int64)
    c = close.as_unit("ns").asi8
    nxt = np.searchsorted(ev, c, side="left")
    hours_to = np.full(len(c), np.inf)
    hours_since = np.full(len(c), np.inf)
    has_next = nxt < len(ev)
    hours_to[has_next] = (ev[nxt[has_next]] - c[has_next]) / 3.6e12
    has_prev = nxt > 0
    hours_since[has_prev] = (c[has_prev] - ev[nxt[has_prev] - 1]) / 3.6e12
    return pd.DataFrame({"hours_to_event": hours_to, "hours_since_event": hours_since}, index=df.index)
