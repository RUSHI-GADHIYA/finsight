"""Minimal async client for SEC EDGAR.

SEC fair-access rules: max 10 requests/second and a descriptive User-Agent with a contact
email. See https://www.sec.gov/os/accessing-edgar-data
"""

import asyncio
import time
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SUBMISSIONS_PAGE_URL = "https://data.sec.gov/submissions/{name}"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/{document}"


@dataclass(frozen=True)
class FilingRef:
    ticker: str
    cik: str
    form: str
    accession_no: str
    filing_date: date
    report_date: date | None
    url: str


class _RateLimiter:
    def __init__(self, per_second: float) -> None:
        self._interval = 1.0 / per_second
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self) -> None:
        async with self._lock:
            delay = self._last + self._interval - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._last = time.monotonic()


class EdgarClient:
    def __init__(
        self,
        user_agent: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        requests_per_second: float = 8,
    ) -> None:
        self._http = httpx.AsyncClient(
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=30,
            follow_redirects=True,
            transport=transport,
        )
        self._limiter = _RateLimiter(requests_per_second)
        self._cik_by_ticker: dict[str, str] | None = None

    async def __aenter__(self) -> "EdgarClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._http.aclose()

    async def _get(self, url: str) -> httpx.Response:
        await self._limiter.wait()
        resp = await self._http.get(url)
        resp.raise_for_status()
        return resp

    async def cik_for(self, ticker: str) -> str:
        if self._cik_by_ticker is None:
            data = (await self._get(TICKERS_URL)).json()
            self._cik_by_ticker = {
                row["ticker"].upper(): str(row["cik_str"]).zfill(10) for row in data.values()
            }
        try:
            return self._cik_by_ticker[ticker.upper()]
        except KeyError:
            raise ValueError(f"Unknown ticker: {ticker}") from None

    async def list_filings(
        self, ticker: str, forms: tuple[str, ...] = ("10-K",), limit: int = 2
    ) -> list[FilingRef]:
        """Most recent filings of the given form types, newest first.

        `filings.recent` holds only the latest ~1000 filings; heavy filers (banks issuing
        prospectuses daily) push older 10-Ks into paged `filings.files`, fetched on demand.
        """
        cik = await self.cik_for(ticker)
        filings = (await self._get(SUBMISSIONS_URL.format(cik=cik))).json()["filings"]

        refs: list[FilingRef] = []
        pages = [filings["recent"]]
        older = [f["name"] for f in filings.get("files", [])]
        while pages:
            page = pages.pop(0)
            for i, form in enumerate(page["form"]):
                if form not in forms:
                    continue
                accession = page["accessionNumber"][i]
                report = page["reportDate"][i]
                refs.append(
                    FilingRef(
                        ticker=ticker.upper(),
                        cik=cik,
                        form=form,
                        accession_no=accession,
                        filing_date=date.fromisoformat(page["filingDate"][i]),
                        report_date=date.fromisoformat(report) if report else None,
                        url=ARCHIVE_URL.format(
                            cik_int=int(cik),
                            accession=accession.replace("-", ""),
                            document=page["primaryDocument"][i],
                        ),
                    )
                )
                if len(refs) >= limit:
                    return refs
            if not pages and older:
                pages.append(
                    (await self._get(SUBMISSIONS_PAGE_URL.format(name=older.pop(0)))).json()
                )
        return refs

    async def company_facts(self, ticker: str) -> dict[str, Any]:
        """All XBRL facts reported by the company (the structured numbers behind filings)."""
        cik = await self.cik_for(ticker)
        data: dict[str, Any] = (await self._get(COMPANY_FACTS_URL.format(cik=cik))).json()
        return data

    async def fetch_document(self, url: str) -> str:
        return (await self._get(url)).text
