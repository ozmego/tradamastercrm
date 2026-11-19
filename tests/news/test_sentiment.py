from datetime import UTC, datetime

import pytest

from tradecrm.news.models import NewsItem
from tradecrm.news.sentiment import compute_sentiment_score


def _item(positive: int, negative: int, important: int = 0, total: int | None = None) -> NewsItem:
    return NewsItem(
        id="1",
        title="t",
        url="u",
        published_at=datetime.now(UTC),
        currencies=["BTC"],
        positive_votes=positive,
        negative_votes=negative,
        important_votes=important,
        total_votes=total if total is not None else positive + negative + important,
    )


class TestLowConfidenceFallback:
    def test_too_few_items_returns_low_confidence(self):
        items = [_item(5, 0), _item(5, 0)]  # 2 items, esik 3
        result = compute_sentiment_score(items, window_hours=6, min_votes_threshold=1, min_items_threshold=3)
        assert result.low_confidence is True
        assert result.score == 0.0

    def test_too_few_votes_returns_low_confidence(self):
        items = [_item(1, 0), _item(0, 1), _item(1, 0)]  # 3 item ama toplam oy dusuk
        result = compute_sentiment_score(items, window_hours=6, min_votes_threshold=10, min_items_threshold=3)
        assert result.low_confidence is True
        assert result.score == 0.0

    def test_empty_items_is_low_confidence(self):
        result = compute_sentiment_score([], window_hours=6)
        assert result.low_confidence is True
        assert result.sample_size == 0


class TestWeightedScore:
    def test_all_positive_items_score_near_one(self):
        items = [_item(10, 0) for _ in range(3)]
        result = compute_sentiment_score(items, window_hours=6, min_votes_threshold=1, min_items_threshold=1)
        assert result.low_confidence is False
        assert result.score == pytest.approx(1.0)

    def test_all_negative_items_score_near_minus_one(self):
        items = [_item(0, 10) for _ in range(3)]
        result = compute_sentiment_score(items, window_hours=6, min_votes_threshold=1, min_items_threshold=1)
        assert result.score == pytest.approx(-1.0)

    def test_balanced_items_score_near_zero(self):
        items = [_item(10, 10) for _ in range(3)]
        result = compute_sentiment_score(items, window_hours=6, min_votes_threshold=1, min_items_threshold=1)
        assert result.score == pytest.approx(0.0, abs=1e-9)

    def test_high_vote_item_dominates_low_vote_item(self):
        # Bir haber cok oylanmis ve pozitif, digeri az oylanmis ve negatif.
        items = [_item(100, 0), _item(0, 2)]
        result = compute_sentiment_score(items, window_hours=6, min_votes_threshold=1, min_items_threshold=1)
        assert result.score > 0.5

    def test_score_always_clamped_to_valid_range(self):
        items = [_item(1000, 0), _item(0, 1000), _item(500, 500)]
        result = compute_sentiment_score(items, window_hours=6, min_votes_threshold=1, min_items_threshold=1)
        assert -1.0 <= result.score <= 1.0
