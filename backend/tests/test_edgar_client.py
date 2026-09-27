from datetime import date

import httpx
import pytest

from app.rag.edgar_client import EdgarClient

TICKERS = {"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"}}
SUBMISSIONS = {
    "filings": {
        "recent": {
            "form": ["10-Q", "10-K", "8-K", "10-K"],
            "accessionNumber": ["0001-24-1", "0001-24-2", "0001-24-3", "0001-23-4"],
            "filingDate": ["2024-11-20", "2024-02-21", "2024-02-01", "2023-02-24"],
            "reportDate": ["2024-10-27", "2024-01-28", "", "2023-01-29"],
            "primaryDocument": ["q.htm", "k24.htm", "8k.htm", "k23.htm"],
        }
    }
}


def _handler(request: httpx.Request) -> httpx.Response:
    assert request.headers["User-Agent"] == "test agent test@example.com"
    if request.url.path == "/files/company_tickers.json":
        return httpx.Response(200, json=TICKERS)
    if request.url.path == "/submissions/CIK0001045810.json":
        return httpx.Response(200, json=SUBMISSIONS)
    return httpx.Response(404)


@pytest.fixture
async def client() -> EdgarClient:
    return EdgarClient(
        "test agent test@example.com",
        transport=httpx.MockTransport(_handler),
        requests_per_second=1000,
    )


async def test_list_filings_filters_forms_and_builds_archive_url(client: EdgarClient) -> None:
    refs = await client.list_filings("nvda", forms=("10-K",), limit=5)

    assert [r.accession_no for r in refs] == ["0001-24-2", "0001-23-4"]
    first = refs[0]
    assert first.cik == "0001045810"
    assert first.filing_date == date(2024, 2, 21)
    assert first.url == "https://www.sec.gov/Archives/edgar/data/1045810/0001242/k24.htm"


async def test_limit_is_respected(client: EdgarClient) -> None:
    refs = await client.list_filings("NVDA", forms=("10-K", "10-Q"), limit=1)
    assert [r.form for r in refs] == ["10-Q"]


async def test_unknown_ticker_raises(client: EdgarClient) -> None:
    with pytest.raises(ValueError, match="Unknown ticker"):
        await client.cik_for("ZZZZ")


async def test_list_filings_pages_into_older_submissions() -> None:
    recent = {k: v[:1] for k, v in SUBMISSIONS["filings"]["recent"].items()}  # only a 10-Q
    older = {k: v[1:] for k, v in SUBMISSIONS["filings"]["recent"].items()}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/files/company_tickers.json":
            return httpx.Response(200, json=TICKERS)
        if path == "/submissions/CIK0001045810.json":
            files = [{"name": "CIK0001045810-submissions-001.json"}]
            return httpx.Response(200, json={"filings": {"recent": recent, "files": files}})
        if path == "/submissions/CIK0001045810-submissions-001.json":
            return httpx.Response(200, json=older)
        return httpx.Response(404)

    client = EdgarClient("ua", transport=httpx.MockTransport(handler), requests_per_second=1000)
    refs = await client.list_filings("NVDA", forms=("10-K",), limit=2)
    assert [r.accession_no for r in refs] == ["0001-24-2", "0001-23-4"]
