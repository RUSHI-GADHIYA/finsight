"""Share-price history from Yahoo Finance via yfinance (unofficial, free, no key).

Prices are context for the report, not a citable SEC source, so failures here should
degrade the report ("price data unavailable") rather than fail the research run.
"""

import asyncio
from datetime import date

from pydantic import BaseModel

PERIODS = ("6mo", "1y", "2y", "5y")


class MonthlyClose(BaseModel):
    month: date
    close: float


class PriceSummary(BaseModel):
    ticker: str
    period: str
    start_close: float
    end_close: float
    total_return: float  # end / start - 1
    monthly: list[MonthlyClose]
    source: str = "Yahoo Finance (yfinance), split/dividend-adjusted closes"


class PriceDataError(RuntimeError):
    pass


def _fetch(ticker: str, period: str) -> PriceSummary:
    import yfinance as yf  # heavy import (pandas); only needed when prices are requested

    history = yf.Ticker(ticker).history(period=period, interval="1mo", auto_adjust=True)
    closes = history["Close"].dropna() if "Close" in history else None
    if closes is None or len(closes) < 2:
        raise PriceDataError(f"No price history for {ticker!r}")
    monthly = [MonthlyClose(month=ts.date(), close=round(float(v), 2)) for ts, v in closes.items()]
    start, end = monthly[0].close, monthly[-1].close
    return PriceSummary(
        ticker=ticker,
        period=period,
        start_close=start,
        end_close=end,
        total_return=round(end / start - 1, 4),
        monthly=monthly,
    )


async def get_price_history(ticker: str, period: str = "2y") -> PriceSummary:
    if period not in PERIODS:
        raise ValueError(f"period must be one of {PERIODS}")
    return await asyncio.to_thread(_fetch, ticker.upper(), period)
