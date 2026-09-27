#!/bin/sh
# Snapshot the local corpus and signed-off reports into backend/seed/ (run from the repo root
# with `docker compose up -d postgres`). Chunk IDs are preserved, so evals/golden_set.jsonl
# stays valid. Re-export after re-ingesting, then regenerate the golden set.
set -eu
docker compose exec -T postgres pg_dump -U finsight -d finsight -Fc -Z 9 --data-only \
  -t filings -t chunks -t reports > backend/seed/finsight-seed.dump
ls -l backend/seed/finsight-seed.dump
