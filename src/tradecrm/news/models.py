from dataclasses import dataclass
from datetime import datetime


@dataclass
class NewsItem:
    id: str
    title: str
    url: str
    published_at: datetime
    currencies: list[str]
    positive_votes: int
    negative_votes: int
    important_votes: int
    total_votes: int
    source: str = ""


@dataclass
class SentimentResult:
    score: float  # [-1, 1], -1 tamamen negatif, +1 tamamen pozitif
    sample_size: int  # pencere icindeki haber sayisi
    total_votes: int
    window_hours: int
    low_confidence: bool  # yetersiz oy/haber sayisi -> score anlamli degil (0 dondurulur)
    as_of: datetime
