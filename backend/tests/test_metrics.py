import json
from pathlib import Path

from app.api.metrics import latest_evals
from app.observability.ops import percentile


def test_percentile_is_nearest_rank() -> None:
    assert percentile([], 0.5) is None
    assert percentile([40], 0.95) == 40
    values = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    assert percentile(values, 0.5) == 50
    assert percentile(values, 0.95) == 100
    assert percentile([3, 1, 2], 0.5) == 2  # unsorted input


def test_latest_evals_reads_json_results_and_skips_broken_files(tmp_path: Path) -> None:
    (tmp_path / "retrieval.json").write_text(json.dumps({"hit_at_5": 0.84}), encoding="utf-8")
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "notes.md").write_text("# ignored", encoding="utf-8")
    assert latest_evals(tmp_path) == {"retrieval": {"hit_at_5": 0.84}}
