from datetime import date

from sqlalchemy.dialects import postgresql

from app.rag.retrieval import SearchFilters, filter_clauses, reciprocal_rank_fusion


def test_rrf_rewards_items_ranked_by_both_retrievers() -> None:
    vector = [1, 2, 3]
    keyword = [3, 4, 1]
    # 1 and 3 appear in both lists and outrank single-list items.
    assert reciprocal_rank_fusion([vector, keyword])[:2] == [1, 3]


def test_rrf_ties_keep_first_seen_order() -> None:
    assert reciprocal_rank_fusion([[10, 20], [20, 10]]) == [10, 20]


def test_rrf_single_ranking_is_identity() -> None:
    assert reciprocal_rank_fusion([[5, 4, 3]]) == [5, 4, 3]
    assert reciprocal_rank_fusion([]) == []


def _sql(filters: SearchFilters) -> list[str]:
    return [
        str(c.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))  # type: ignore[no-untyped-call]
        for c in filter_clauses(filters)
    ]


def test_filter_clauses() -> None:
    assert _sql(SearchFilters()) == []
    clauses = _sql(
        SearchFilters(tickers=["nvda"], sections=["mdna"], forms=["10-K"], since=date(2024, 1, 1))
    )
    assert clauses == [
        "filings.ticker IN ('NVDA')",
        "chunks.section IN ('mdna')",
        "filings.form IN ('10-K')",
        "filings.report_date >= '2024-01-01'",
    ]
