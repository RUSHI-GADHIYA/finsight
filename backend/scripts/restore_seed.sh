#!/bin/sh
# Restore the bundled corpus snapshot (24 10-Ks, 4,361 passages, signed-off reports) into an
# empty, migrated database. Runs inside a postgres:17 image (the dump needs pg_restore 17):
#   docker compose --profile app up      (the `seed` service runs this)
# Skips if the database already has filings, so it is safe to run on every start.
set -eu
: "${PGURL:?set PGURL, e.g. postgresql://finsight:finsight@postgres:5432/finsight}"
DUMP="${SEED_DUMP:-/seed/finsight-seed.dump}"

filings=$(psql "$PGURL" -tAc "select count(*) from filings")
if [ "$filings" -gt 0 ]; then
  echo "seed: database already has $filings filings; nothing to do"
  exit 0
fi
pg_restore --data-only --no-owner --dbname "$PGURL" "$DUMP"
echo "seed: restored $(psql "$PGURL" -tAc "select count(*) from chunks") passages"
