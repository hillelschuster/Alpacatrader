#!/usr/bin/env python3
# SUPERSEDED 2026-10-02 (owner reanchor): do not freeze or validate the
# old-framing policy pipeline. See researches/LIFECYCLE-REANCHOR.md.
"""Freeze the entire lifecycle feature/model/action pipeline before second-half outcomes.

The lock binds code, models, action specs, chronological split and constructed input
manifests. Merely building second-half observations is not outcome evaluation.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze(root: Path, actions: list[Path], primary: Path) -> dict:
    dest = root / "FREEZE.json"
    if dest.exists():
        raise ValueError("a pipeline is already frozen; do not replace it after seeing test")
    split_path = root / "split.json"
    split = json.loads(split_path.read_text())
    model_path = root / "models" / "model_config.json"
    config = json.loads(model_path.read_text())
    if config["training_days"] != split["discovery_days"]:
        raise ValueError("models were not trained on exactly the frozen first533 days")
    if len(split["discovery_days"]) != 533 or len(split["validation_days"]) != 533:
        raise ValueError("chronology size changed")
    if primary not in actions:
        raise ValueError("primary action spec must be in the declared evaluated surface")
    sources = {}
    scripts = Path(__file__).resolve().parent
    for name in ("lifecycle_build.py", "lifecycle_tape.py", "lifecycle_study.py",
                 "lifecycle_anatomy.py", "lifecycle_features.py", "lifecycle_models.py",
                 "lifecycle_economics.py", "lifecycle_policy.py", "lifecycle_results.py"):
        p = scripts / name
        sources[str(p)] = sha(p)
    models = {}
    for key, value in config["models"].items():
        path = Path(value["path"])
        if sha(path) != value["sha256"]:
            raise ValueError(f"model changed: {key}")
        models[str(path)] = value["sha256"]
    action_hashes = {}
    maps = {}
    for p in actions:
        spec = json.loads(p.read_text())
        if spec["model_config_sha256"] != sha(model_path):
            raise ValueError("action spec is attached to a different model pipeline")
        state_path = p.parent / "state_map.json"
        if sha(state_path) != spec["state_map_sha256"]:
            raise ValueError("state table changed since action selection")
        action_hashes[str(p)] = sha(p)
        maps[str(state_path)] = sha(state_path)
    data_hashes = {}
    for day in split["discovery_days"] + split["validation_days"]:
        for p in (root / "_done" / f"{day}.json", root / "manifest" / f"{day}.json"):
            if not p.exists():
                raise ValueError(f"construction coverage incomplete: {p}")
            data_hashes[str(p)] = sha(p)
    record = {"study": "LIFECYCLE-01", "status": "FROZEN",
              "frozen_utc": datetime.now(timezone.utc).isoformat(),
              "split_sha256": sha(split_path), "model_config_sha256": sha(model_path),
              "sources": sources, "models": models, "state_maps": maps,
              "action_specs": action_hashes, "primary_action_spec": str(primary),
              "constructed_input_manifests": data_hashes,
              "discovery_days": split["discovery_days"], "validation_days": split["validation_days"],
              "validation_policy": "all declared candidates run unchanged; primary selected on discovery; no refitting",
              "prior_data_exposure": "chronological holdout for this pipeline, not globally pristine: prior studies used full dev estate"}
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2) + "\n")
    tmp.replace(dest)
    return record


def verify(root: Path) -> dict:
    """Re-check every artifact bound by the freeze; called before any test-half loading."""
    dest = root / "FREEZE.json"
    if not dest.exists():
        raise SystemExit(f"validation locked: no pipeline freeze at {dest}")
    record = json.loads(dest.read_text())
    checked = 0
    for group in ("sources", "models", "state_maps", "action_specs"):
        for path, want in record.get(group, {}).items():
            if sha(Path(path)) != want:
                raise ValueError(f"frozen {group} artifact changed after freeze: {path}")
            checked += 1
    for path, want in record.get("constructed_input_manifests", {}).items():
        if sha(Path(path)) != want:
            raise ValueError(f"constructed corpus manifest changed after freeze: {path}")
        checked += 1
    if sha(root / "split.json") != record["split_sha256"]:
        raise ValueError("chronology changed after freeze")
    if sha(root / "models" / "model_config.json") != record["model_config_sha256"]:
        raise ValueError("frozen model config changed after freeze")
    return record


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--actions", type=Path, nargs="+", required=True)
    ap.add_argument("--primary", type=Path, required=True)
    a = ap.parse_args()
    result = freeze(a.root, a.actions, a.primary)
    print(f"FROZEN sources={len(result['sources'])} models={len(result['models'])} "
          f"action_specs={len(result['action_specs'])}; second533 evaluation now permitted")


if __name__ == "__main__":
    main()
