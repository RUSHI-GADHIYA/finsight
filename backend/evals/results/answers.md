# Answer eval

8 golden questions through the full agent graph (supervisor → filings + market → analyst ⇄ critic), stopped at the human-review step; run 2026-09-26. Models: gpt-5-mini (supervisor, critic, judge), gpt-5 (analyst).

| Metric | Value |
|---|---|
| Evidence hit (golden passage retrieved by the agent) | 0.88 |
| Citation hit (golden passage cited in the report) | 0.88 |
| Faithfulness (summary + claims supported by their sources, 47 judged) | 1.00 |
| Mean revisions per report | 0.00 |
| Mean cost per question (incl. judge) | $0.0145 |
| Mean latency per question | 39s |

Citation hit is strict: each question was generated from one passage, and a report can answer correctly from other passages.

| # | Ticker | Evidence | Cited | Faithful | Revisions | Cost |
|---|---|---|---|---|---|---|
| 1 | AAPL | ✓ | ✓ | 3/3 | 0 | $0.0100 |
| 2 | AMD | ✓ | ✓ | 13/13 | 0 | $0.0243 |
| 3 | AMZN | ✗ | ✗ | 14/14 | 0 | $0.0261 |
| 4 | GOOGL | ✓ | ✓ | 3/3 | 0 | $0.0117 |
| 5 | GS | ✓ | ✓ | 4/4 | 0 | $0.0122 |
| 6 | INTC | ✓ | ✓ | 2/2 | 0 | $0.0075 |
| 7 | JPM | ✓ | ✓ | 3/3 | 0 | $0.0131 |
| 8 | META | ✓ | ✓ | 5/5 | 0 | $0.0111 |
