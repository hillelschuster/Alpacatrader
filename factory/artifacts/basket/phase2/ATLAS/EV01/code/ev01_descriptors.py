#!/usr/bin/env python3
"""EV-01 descriptor step (OUTCOME-BLIND).

Pre-registration: researches/PRE-REG-TAPE-ATLAS-EV01.md
  sha256 ce61fcaf8ffcb575c3359b2a3801b227fc2e3af8cae9575fa118b5ae0d2e7d85

This module computes EXACTLY the three frozen member-level descriptors (a/b/c) and
publishes descriptors.parquet + its sha256 before any outcome column is touched.

Guard: this file must never import the ledger/outcome module, never read panel.parquet,
and never name any outcome_*/v_*/future_*/giveback_*/censor column. The only inputs are the
five frozen hand-view density arrays, objects.parquet, rare_refs.parquet, memberships.parquet
and the frozen causal embedding archive.

Descriptor (c) is computed inline with the geometry producer's frozen identity imported from
factory/scripts/basket_tape_atlas_geometry.py (embedding_gram / masked_sq_dists /
_same_tape_columns) -- the producer is NOT re-run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import resource
import sys
from pathlib import Path

import numpy as np
import polars as pl

WT_ROOT = Path("/home/hillel/.config/opencode/worktrees/Alpacatrader/basket-phase2-f1")
DATA_ROOT = Path("/home/hillel/projects/Alpacatrader")

HAND_VIEWS = ("magnitude_dominant", "normalized_shape_dominant", "duration_event_dominant",
              "activity_microstructure_dominant", "balanced_multichannel_reference")
BALANCED = "balanced_multichannel_reference"

MEMBER_KEYS = ("sleeve_day", "family", "entry_rank", "ticker")

# frozen learned-view pass parameters (pre-reg section 4c)
K = 100
CHUNK_ROWS = 512


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wt-root", type=Path, default=WT_ROOT)
    ap.add_argument("--data-root", type=Path, default=DATA_ROOT)
    ap.add_argument("--out", type=Path,
                    default=WT_ROOT / "factory/artifacts/basket/phase2/ATLAS/EV01")
    args = ap.parse_args(argv)

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    geo = args.data_root / "data/atlas/geometry/v1"
    obs = args.data_root / "data/atlas/observation/v0"
    seq = args.data_root / "data/atlas/sequence/v1/runs/causal/block"

    # ---- frozen inputs ---------------------------------------------------------------
    # objects.parquet row order IS the index of every view_*.nn.npz array -- never re-sort it.
    objects = pl.read_parquet(geo / "objects.parquet")
    memberships = pl.read_parquet(obs / "memberships.parquet")
    object_ids = objects["object_id"].to_list()
    n_obj = len(object_ids)

    # ---- (a) balanced-view density, indexed by object row ----------------------------
    dens = {v: np.load(geo / f"view_{v}.nn.npz")["density"] for v in HAND_VIEWS}
    for v, d in dens.items():
        assert d.shape == (n_obj,), (v, d.shape)

    # ---- (b) rarity: density == 0 in ANY of the five views (the frozen rare_refs rule;
    #      (verified: the all-five-zero set is empty because normalized_shape has zero
    #      objects with density 0, so the only non-degenerate reading of the rule is ANY))
    zero = np.stack([dens[v] == 0 for v in HAND_VIEWS], axis=1)  # (n_obj, 5)
    obj_rare = zero.any(axis=1)
    obj_views_zero = np.array([",".join(HAND_VIEWS[j] for j in range(5) if zero[i, j])
                               for i in range(n_obj)], dtype=object)

    # cross-check (b) against the published rare_refs artifact
    rare_refs = pl.read_parquet(geo / "rare_refs.parquet")
    rr_map = dict(zip(rare_refs["object_id"].to_list(), rare_refs["views_unique"].to_list()))
    calc_rare_ids = {object_ids[i] for i in np.flatnonzero(obj_rare)}
    rr_ids = set(rr_map)
    assert calc_rare_ids == rr_ids, "rare_refs rule set mismatch"
    vu_mismatch = 0
    for i in np.flatnonzero(obj_rare):
        a = tuple(sorted(str(obj_views_zero[i]).split(",")))
        b = tuple(sorted(str(rr_map[object_ids[i]]).split(",")))
        if a != b:
            vu_mismatch += 1
    assert vu_mismatch == 0, f"views_unique set mismatch on {vu_mismatch} rows"

    # ---- (c) learned-view nearest-neighbour distance ----------------------------------
    sys.path.insert(0, str(args.wt_root / "factory/scripts"))
    import basket_tape_atlas_geometry as G  # noqa: E402  (frozen identity, not re-derived)

    with np.load(seq / "embeddings.npz", allow_pickle=True) as z:
        emb_ids = [str(x) for x in z["object_ids"]]
        values = np.asarray(z["values"], dtype=np.float64)
        valid = np.asarray(z["valid"]).astype(bool)
    assert len(set(emb_ids)) == len(emb_ids)
    # align the archive to objects.parquet order via object_ids
    order = {o: i for i, o in enumerate(emb_ids)}
    assert set(object_ids) <= set(order), "embeddings do not cover the object set"
    sel = np.array([order[o] for o in object_ids], dtype=np.int64)
    values, valid = values[sel], valid[sel]
    del sel

    block = objects["block"].to_numpy()
    fit_mask = block == "block1"          # representation_matrix_v1: fit_blocks=[block1]
    # z-score every embedding channel on fit-block valid entries only (frozen embedding_gram)
    u_z, q_z2, m_valid, _m, estats = G.embedding_gram(
        {"values": values, "valid": valid}, fit_mask=fit_mask)
    del values, valid
    pid = objects["path_id"].to_list()
    pcode = {p: i for i, p in enumerate(dict.fromkeys(pid))}
    path_of = np.fromiter((pcode[p] for p in pid), dtype=np.int64, count=len(pid))
    tensors = {"path_of": path_of}
    same_tape = G._same_tape_columns(tensors)

    nn_dist0 = np.full(n_obj, np.nan, dtype=np.float64)
    n_comparable = np.zeros(n_obj, dtype=np.int64)
    for s in range(0, n_obj, CHUNK_ROWS):
        e = min(n_obj, s + CHUNK_ROWS)
        dist2 = G.masked_sq_dists(u_z, q_z2, m_valid, slice(s, e))
        for r in range(e - s):
            cols = same_tape[s + r]
            if cols.size:
                dist2[r, cols] = np.inf
        n_comparable[s:e] = np.count_nonzero(np.isfinite(dist2), axis=1)
        idx, val = G.topk_from_dist(dist2, K)
        # descriptor = smallest finite distance = nn_dist[:,0]
        nn_dist0[s:e] = val[:, 0]
        del dist2, idx, val
    del u_z, q_z2, m_valid, same_tape

    obj_tbl = pl.DataFrame({
        "object_id": objects["object_id"],
        "path_id": objects["path_id"],
        "block": objects["block"],
        "scale": objects["scale"],
        "density_balanced": dens[BALANCED],
        "object_rare": obj_rare.astype(np.int8),
        "learned_nn": nn_dist0,
        "learned_n_comparable": n_comparable,
    })

    # ---- aggregate object descriptors to the member (unweighted mean over 3 scales) ----
    agg = obj_tbl.group_by("path_id").agg(
        pl.col("density_balanced").mean().alias("d_density_balanced"),
        pl.col("object_rare").max().alias("d_rare"),
        pl.col("learned_nn").mean().alias("d_learned_nn"),
        pl.col("learned_nn").is_finite().all().alias("all_scales_finite"),
        pl.len().alias("n_objects"),
    )
    desc = memberships.select(["member_id", "day", "family", "entry_rank", "ticker",
                               "path_id", "block", "month"]).rename(
        {"day": "sleeve_day"}).join(agg, on="path_id", how="left")
    assert desc.height == 6160
    assert desc["d_density_balanced"].null_count() == 0
    assert desc["d_rare"].null_count() == 0
    assert desc["n_objects"].min() == 3 and desc["n_objects"].max() == 3

    desc = desc.with_columns([
        pl.col("d_density_balanced").cast(pl.Float64),
        pl.col("d_rare").cast(pl.Int8),
        pl.col("d_learned_nn").cast(pl.Float64),
    ]).select(["member_id", *MEMBER_KEYS, "path_id", "block", "month",
               "d_density_balanced", "d_rare", "d_learned_nn"]).sort(
        ["sleeve_day", "family", "entry_rank", "ticker"])

    dpath = out / "descriptors.parquet"
    desc.write_parquet(dpath)
    dsha = sha256(dpath)

    attestation = {
        "artifact": "descriptors.parquet",
        "sha256": dsha,
        "rows": desc.height,
        "columns": desc.columns,
        "outcome_read": False,
        "statement": ("No outcome column was read. This step read only the five frozen "
                      "view_*.nn.npz density arrays, objects.parquet, rare_refs.parquet, "
                      "memberships.parquet and the frozen causal embedding archive. "
                      "panel.parquet and the ledger module were not opened."),
        "descriptors": {
            "a_density_balanced": "unweighted mean of balanced-view per-object density over the "
                                  "member's three scale objects",
            "b_rare": "1 iff ANY of the member's three objects has density==0 in ANY of the five "
                      "published views (frozen rare_refs rule); verified set-identical to "
                      "rare_refs.parquet (8,577 objects)",
            "c_learned_nn": "unweighted mean over the member's three objects of the smallest "
                            "finite masked squared-L2 distance in the frozen causal learned view "
                            "(embedding_gram + masked_sq_dists, same-tape exclusion, k=100, "
                            "chunk_rows=512)",
        },
        "learned_view_fit": {"fit_blocks": ["block1"], "k": K, "chunk_rows": CHUNK_ROWS,
                             "channel_stats": estats},
        "cross_checks": {
            "rare_refs_rule_set_equal": True,
            "median_density_balanced_block1": float(np.median(
                obj_tbl.filter(pl.col("block") == "block1")["density_balanced"].to_numpy())),
            "median_density_balanced_block2": float(np.median(
                obj_tbl.filter(pl.col("block") == "block2")["density_balanced"].to_numpy())),
            "learned_nn_nonfinite_objects": int((~np.isfinite(nn_dist0)).sum()),
        },
        "inputs": {
            "objects.parquet": sha256(geo / "objects.parquet"),
            "rare_refs.parquet": sha256(geo / "rare_refs.parquet"),
            "memberships.parquet": sha256(obs / "memberships.parquet"),
            "embeddings_causal.npz": sha256(seq / "embeddings.npz"),
            "view_*.nn.npz": {v: sha256(geo / f"view_{v}.nn.npz") for v in HAND_VIEWS},
        },
        "code": {"ev01_descriptors.py": sha256(Path(__file__).resolve())},
        "peak_rss_mb": peak_rss_mb(),
    }
    (out / "descriptors.sha256").write_text(dsha + "  descriptors.parquet\n")
    (out / "descriptors_attestation.json").write_text(json.dumps(attestation, indent=1))

    # object-level table kept beside the member table for traceability (not a published
    # descriptor: only the member-level aggregation is the frozen unit)
    obj_tbl.write_parquet(out / "object_descriptors.parquet")

    print(f"descriptors.parquet sha256={dsha}")
    print(f"rows={desc.height} learned_nn_nonfinite_objects={attestation['cross_checks']['learned_nn_nonfinite_objects']}")
    print(f"median density block1={attestation['cross_checks']['median_density_balanced_block1']} "
          f"block2={attestation['cross_checks']['median_density_balanced_block2']}")
    print(f"peak_rss_mb={attestation['peak_rss_mb']:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
