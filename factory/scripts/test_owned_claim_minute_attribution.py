"""A relocated ledger must match the declared vintage actually consumed."""

import json
import shutil

import owned_claim_minute_attribution as reader
import polars as pl
import pytest


@pytest.mark.parametrize("changed_book", tuple(reader.PRIOR_BOOKS.values()))
def test_relocated_prior_book_drift_is_rejected(tmp_path, monkeypatch, changed_book):
    consumed = tmp_path / "consumed"
    declared = tmp_path / "declared"
    artifacts = tmp_path / "artifacts"
    stage = tmp_path / "stage"
    for path in (consumed, declared, artifacts, stage):
        path.mkdir()
    books = {}
    for name, book in reader.PRIOR_BOOKS.items():
        original = declared / book
        local = consumed / book
        original.mkdir()
        local.mkdir()
        for filename in ("daily.parquet", "summary.parquet"):
            pl.DataFrame({"day": ["2021-09-03"], "ret": [0.02]}).write_parquet(
                original / filename
            )
        metadata = original / "metadata.json"
        metadata.write_text("{}")
        shutil.copyfile(original / "daily.parquet", local / "daily.parquet")
        books[name] = {
            "root": str(original),
            "model_metadata_path": str(metadata),
            "daily_sha256": reader.sha256_file(original / "daily.parquet"),
            "summary_sha256": reader.sha256_file(original / "summary.parquet"),
            "model_metadata_sha256": reader.sha256_file(metadata),
        }
    minute = consumed / reader.MINUTE_BOOK
    minute.mkdir()
    pl.DataFrame({"day": ["2021-09-03"], "ret": [0.0]}).write_parquet(
        minute / "daily.parquet"
    )
    harvest = consumed / reader.HARVESTABILITY
    harvest.parent.mkdir()
    pl.DataFrame({"day": ["2021-09-03"]}).write_parquet(harvest)
    loss = artifacts / reader.LOSS_ARTIFACT
    loss.write_text(
        json.dumps(
            {
                "kind": reader.KIND,
                "run_id": "fixture",
                "producer_sha256": "fixture-source",
                "inputs": {
                    "books": books,
                    "harvestability_events_sha256": reader.sha256_file(harvest),
                    "split_sha256": "fixture-split",
                },
            }
        )
    )
    (artifacts / reader.GAP_ARTIFACT).write_text(
        json.dumps(
            {
                "kind": reader.KIND,
                "inputs": {"artifact_sha256": reader.sha256_file(loss)},
            }
        )
    )
    monkeypatch.setattr(reader, "book_dir", lambda _root, book: consumed / book)
    monkeypatch.setattr(reader, "stage_dir", lambda _root: stage)
    monkeypatch.setattr(reader.ls, "v2_dir", lambda _root: consumed)
    _, _, pins = reader.verify_inputs(consumed, artifacts, [])
    name = next(name for name, book in reader.PRIOR_BOOKS.items() if book == changed_book)
    assert pins["books"][changed_book]["daily_sha256"] == books[name]["daily_sha256"]
    assert pins["books"][changed_book]["lineage_status"] == "DECLARED_VINTAGE_VERIFIED"

    changed = consumed / changed_book / "daily.parquet"
    pl.read_parquet(changed).with_columns((pl.col("ret") + 0.123).alias("ret")).write_parquet(
        changed
    )
    assert reader.sha256_file(declared / changed_book / "daily.parquet") == books[name][
        "daily_sha256"
    ]
    with pytest.raises(SystemExit, match=f"{name} consumed daily book differs"):
        reader.verify_inputs(consumed, artifacts, [])
