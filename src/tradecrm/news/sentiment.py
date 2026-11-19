from datetime import UTC, datetime

from tradecrm.news.models import NewsItem, SentimentResult


def compute_sentiment_score(
    items: list[NewsItem],
    window_hours: int,
    min_votes_threshold: int = 5,
    min_items_threshold: int = 3,
) -> SentimentResult:
    """Her haberin pozitif/negatif oy farkini toplam oyla agirliklandirip
    [-1, 1] araliginda tek bir skora indirger. Toplam oy/haber sayisi
    esigin altindaysa low_confidence=True ile notr (0) doner -- az sayida
    oydan asiri yorum cikarilmaz."""
    now = datetime.now(UTC)
    total_votes = sum(item.total_votes for item in items)

    if len(items) < min_items_threshold or total_votes < min_votes_threshold:
        return SentimentResult(
            score=0.0,
            sample_size=len(items),
            total_votes=total_votes,
            window_hours=window_hours,
            low_confidence=True,
            as_of=now,
        )

    weighted_sum = 0.0
    weight_total = 0.0
    for item in items:
        pos_neg_total = item.positive_votes + item.negative_votes
        item_score = (item.positive_votes - item.negative_votes) / max(pos_neg_total, 1)
        weight = item.total_votes + 1  # smoothing: 0 oylu haberler de az agirlikla katkida bulunur
        weighted_sum += item_score * weight
        weight_total += weight

    score = weighted_sum / weight_total if weight_total > 0 else 0.0
    score = max(-1.0, min(1.0, score))

    return SentimentResult(
        score=score,
        sample_size=len(items),
        total_votes=total_votes,
        window_hours=window_hours,
        low_confidence=False,
        as_of=now,
    )
