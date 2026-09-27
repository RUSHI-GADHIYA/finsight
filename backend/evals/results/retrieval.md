# Retrieval eval

25 generated questions over 12 companies' 10-Ks, filtered to the question's ticker; run 2026-09-25. Hit@5: source passage in the top 5. MRR@10: mean reciprocal rank.

| Config | Hit@5 | MRR@10 | Latency / query (CPU) |
|---|---|---|---|
| vector | 0.72 | 0.57 | 0.03s |
| hybrid (RRF) | 0.80 | 0.61 | 0.06s |
| hybrid + rerank | 0.84 | 0.62 | 3.82s |
