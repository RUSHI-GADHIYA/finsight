from datetime import date
from typing import Any

from app.rag.financials import annual_financials


def _fact(val: float, start: str, end: str, filed: str, form: str = "10-K") -> dict[str, Any]:
    return {"val": val, "start": start, "end": end, "filed": filed, "form": form, "accn": filed}


def _facts(**tags: list[dict[str, Any]]) -> dict[str, Any]:
    return {"facts": {"us-gaap": {tag: {"units": {"USD": v}} for tag, v in tags.items()}}}


FACTS = _facts(
    # Older tag for FY2023, newer tag from FY2024: both must be picked up.
    Revenues=[_fact(80, "2022-02-01", "2023-01-31", "2023-03-01")],
    RevenueFromContractWithCustomerExcludingAssessedTax=[
        _fact(100, "2023-02-01", "2024-01-31", "2024-03-01"),
        _fact(101, "2023-02-01", "2024-01-31", "2025-03-01"),  # restated later: wins
        _fact(30, "2024-11-01", "2025-01-31", "2025-03-01"),  # quarter: ignored
        _fact(150, "2024-02-01", "2025-01-31", "2025-03-01"),
        _fact(999, "2024-02-01", "2025-01-31", "2025-06-01", form="8-K"),  # not a 10-K
    ],
    CostOfRevenue=[_fact(60, "2024-02-01", "2025-01-31", "2025-03-01")],
    GrossProfit=[_fact(50, "2023-02-01", "2024-01-31", "2025-03-01")],
    OperatingIncomeLoss=[_fact(45, "2024-02-01", "2025-01-31", "2025-03-01")],
    NetIncomeLoss=[_fact(30, "2024-02-01", "2025-01-31", "2025-03-01")],
)


def test_annual_financials_picks_latest_full_year_values() -> None:
    rows = annual_financials(FACTS, years=3)

    assert [r.fiscal_year_end for r in rows] == [
        date(2025, 1, 31),
        date(2024, 1, 31),
        date(2023, 1, 31),
    ]
    latest, prior, oldest = rows
    assert latest.revenue == 150
    assert latest.gross_profit == 90  # derived: revenue - cost of revenue
    assert latest.gross_margin == 0.6
    assert latest.operating_margin == 0.3
    assert latest.net_margin == 0.2
    assert prior.revenue == 101 and prior.source_accession == "2025-03-01"
    assert prior.gross_margin == round(50 / 101, 4)
    assert oldest.revenue == 80 and oldest.net_margin is None


def test_years_limit_and_missing_data() -> None:
    assert len(annual_financials(FACTS, years=1)) == 1
    assert annual_financials({"facts": {}}, years=3) == []
