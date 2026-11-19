import pandas as pd

from tradecrm.data.provider import timeframe_to_timedelta


def align_asof(known_at: pd.Series, df: pd.DataFrame, timeframe: str) -> pd.Series:
    """known_at: index'i degerin BILINDIGI an (UTC) olan seri. Her taban
    bar icin, o barin KAPANIS anina kadar bilinen en son deger dondurulur.
    Kapanistan sonra bilinen hicbir deger kullanilmaz (lookahead yok).
    Hic deger yoksa NaN."""
    close_times = df.index + timeframe_to_timedelta(timeframe)
    if known_at is None or len(known_at) == 0:
        return pd.Series(float("nan"), index=df.index)
    s = known_at.sort_index()
    s = s[~s.index.duplicated(keep="last")]
    pos = s.index.searchsorted(close_times, side="right") - 1
    values = s.to_numpy(dtype=float)
    out = [values[p] if p >= 0 else float("nan") for p in pos]
    return pd.Series(out, index=df.index, dtype=float)
