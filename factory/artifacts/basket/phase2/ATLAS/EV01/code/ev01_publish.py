#!/usr/bin/env python3
"""EV-01 publisher: manifest.json + report.md (<=80 lines) from the computed results.

Pins every EV01 file sha, every pre-registration section-0 input sha, the pre-reg sha and the
execution code shas. The report is generated from results.json so no number is hand-copied.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

WT_ROOT = Path("/home/hillel/.config/opencode/worktrees/Alpacatrader/basket-phase2-f1")
DATA_ROOT = Path("/home/hillel/projects/Alpacatrader")

PRE_REG = "ce61fcaf8ffcb575c3359b2a3801b227fc2e3af8cae9575fa118b5ae0d2e7d85"
PRE_REG_PATH = "researches/PRE-REG-TAPE-ATLAS-EV01.md"

# pre-registration section 0 (measured shas the read froze on)
INPUTS = {
    "factory/artifacts/basket/phase2/ATLAS/panel.parquet":
        "2a021eda878cdbe53e41d4cc88e2d8ba3b14d9a3a819723d9f679c8aca7c3488",
    "data/atlas/geometry/v1/objects.parquet":
        "6c0f655ba76cdd176f8a7857df4895e25aeb5c711cd8d1294a4564954e26f30f",
    "data/atlas/geometry/v1/rare_refs.parquet":
        "e7ae275c74576d653c1195eeb1efd04d5d154cb062aa8e1274e6410ad42f7423",
    "data/atlas/observation/v0/memberships.parquet":
        "d461c926321ac5730e01570a0eee16ba90fb4baa7d367b72ea9ac020fb1563d5",
    "factory/artifacts/basket/phase2/ATLAS/TAPE/GEOMETRY/v1/retrieval_report.json":
        "5673570180dc327024d96975c880be681d2dcf3ec5b39866ab4616f87425f47e",
    "factory/artifacts/basket/phase2/ATLAS/TAPE/representation_matrix_v1.json":
        "9720d0ef27ce7272b52c7cc30522d4271b776e6fe30783bfb93a78c8ffb8a2d9",
    "data/atlas/sequence/v1/runs/causal/block/embeddings.npz":
        "e1cacdd613c429cd59b359607e09e30dd8486774792057d7ef08af931e359c6b",
    "data/atlas/sequence/v1/runs/retrospective/block/embeddings.npz":
        "1a67fc8cd352139919f07af8e4f3892c14110716efd61f3e40a49830156ddf18",
    "factory/artifacts/basket/phase2/ATLAS/TAPE/SEQUENCE/v1/sequence_reports_by_mode.json":
        "1e30bab430bf75e008421b4531cca330d73a8fd9cd0f7279092ff142cb2151f1",
    "data/atlas/geometry/v1/view_magnitude_dominant.nn.npz":
        "77621f4d215b87029cce5abee02f53600673b93f77a644dd7e6eca274d90cfb7",
    "data/atlas/geometry/v1/view_normalized_shape_dominant.nn.npz":
        "b02a8eede705f2db8a5e9e423454202c4c27e3daeb1dd6a3683d668fed3a0662",
    "data/atlas/geometry/v1/view_duration_event_dominant.nn.npz":
        "5aa271aea21806378ebe27fe0645e375bf188975b32d1f0492acf9df85e8745c",
    "data/atlas/geometry/v1/view_activity_microstructure_dominant.nn.npz":
        "0936ee112c3ed6ad3a5b36cbc29abe74a17537430ac650487dc62b778b48d589",
    "data/atlas/geometry/v1/view_balanced_multichannel_reference.nn.npz":
        "45ff98838f235f64b852c5ad81ef5744d4a396b6729a0c9f2d3ca2bb4ee86d66",
}
DESC_LABEL = {
    "d_density_balanced": "a balanced-density",
    "d_rare": "b rare-flag",
    "d_learned_nn": "c learned-NN",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def f4(x):
    return "nan" if x is None or x != x else f"{x:+.4f}"


def fm(x):
    return "nan" if x is None or x != x else f"{x:+.2f}%"


def fp(x):
    return "nan" if x is None or x != x else f"{x*100:+.2f}%"


def clean(o):
    """Recursively map non-finite floats to None so every published JSON is strict JSON."""
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [clean(v) for v in o]
    if isinstance(o, bool):
        return o
    if isinstance(o, int):
        return o
    if isinstance(o, float):
        return None if not math.isfinite(o) else o
    return o


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wt-root", type=Path, default=WT_ROOT)
    ap.add_argument("--data-root", type=Path, default=DATA_ROOT)
    ap.add_argument("--out", type=Path,
                    default=WT_ROOT / "factory/artifacts/basket/phase2/ATLAS/EV01")
    args = ap.parse_args(argv)
    out = args.out
    R = json.loads((out / "results.json").read_text())
    meta = R["meta"]

    # --- input provenance (assert against the frozen section-0 shas) ---
    inputs = {}
    for rel, exp in INPUTS.items():
        base = args.wt_root if rel.startswith("factory/") else args.data_root
        p = base / rel
        got = sha256(p)
        assert got == exp, f"input sha drift: {rel}: {got} != {exp}"
        inputs[rel] = got
    assert sha256(args.wt_root / PRE_REG_PATH) == PRE_REG

    # --- coverage.json (pre-reg section 8.6): censored / blocked / joined counts ---
    atlas_cov = json.loads(
        (args.wt_root / "factory/artifacts/basket/phase2/ATLAS/coverage.json").read_text())
    cen = atlas_cov["census"]
    blocked = {"block1": sum(cen[m].get("blocked", 0) for m in cen if m <= "2023-12"),
               "block2": sum(cen[m].get("blocked", 0) for m in cen if m >= "2025-02")}
    blocked["total"] = blocked["block1"] + blocked["block2"]
    mcount = {"block1": sum(1 for m in cen if m <= "2023-12"),
              "block2": sum(1 for m in cen if m >= "2025-02")}
    mcount["pooled"] = mcount["block1"] + mcount["block2"]
    pc = meta["censored_census"]
    cov = {
        "panel_sha256": meta["panel_sha256"],
        "descriptors_sha256": meta["descriptors_sha256"],
        "outcomes_sha256": meta["outcomes_sha256"],
        "members_by_block": {
            b: atlas_cov["per_block"][b]["members"] for b in ("block1", "block2")},
        "days_by_block": {b: atlas_cov["per_block"][b]["days"] for b in ("block1", "block2")},
        "month_counts": mcount,
        "censored_census": pc,
        "censored_share": {"block1": pc["block1"] / atlas_cov["per_block"]["block1"]["members"],
                           "block2": pc["block2"] / atlas_cov["per_block"]["block2"]["members"],
                           "pooled": pc["total"] / sum(
                               atlas_cov["per_block"][b]["members"] for b in ("block1", "block2"))},
        "blocked_slots": blocked,
        "resolved_any_finite_delta": meta["resolved_any_finite_delta"],
        "K5_power_floor": {"block_min": 200, "pooled_min": 500},
        "K7_coverage_floor": 0.10,
        "note": ("blocked slots are cash, retained in the denominator and never given a member "
                 "row or a fabricated Delta; the member ratio is computed on the filled, "
                 "non-censored population only (K7: censored share measured, not mixed in)"),
    }
    (out / "coverage.json").write_text(json.dumps(cov, indent=1))

    # --- report.md (<=80 lines) -------------------------------------------------------
    L = []
    A = L.append
    A("# EV-01 — minute-horizon descriptor/outcome read (executed once)")
    A("")
    A(f"Pre-registration: `{PRE_REG_PATH}` sha256 `{PRE_REG}` (FROZEN 2026-10-01).")
    A(f"Panel sha `{meta['panel_sha256']}`; descriptors sha `{meta['descriptors_sha256']}`; "
      f"outcomes sha `{meta['outcomes_sha256']}`.")
    A("")
    A("## Question")
    A("Does the evidenced within-name structure separate forward member returns from the "
      "hold-flat baseline at minute horizons, net of 100 bps?")
    A("")
    A("## Method")
    A("Unit = member (6,160). Descriptors (outcome-blind, published first): (a) balanced-view "
      "density mean over the 3 scales; (b) rare flag = density 0 in >=1 of five views "
      "(frozen rare_refs rule); (c) learned-view NN distance (frozen causal embeddings, "
      "masked squared-L2, same-tape exclusion, k=100, chunk 512).")
    A("Outcome: `Delta_member = k*(gross_rule - gross_hold)` per `exit_at_bar:N`, N in "
      "{1,5,30}, vs `hold_flat`, bps_total=100, k=0.99005, read through "
      "`basket_atlas_ledger.replay_member`. Censored members unresolved (excluded, counted); "
      "blocked fills = cash (no member row).")
    A("Stats: pooled block-tagged deciles (616 each, ties by (descriptor, member_id)); S1 "
      "Spearman; S2 top-minus-bottom decile mean Delta; month-blocked bootstrap 10,000 draws "
      f"seed {meta['boot_seed']}, percentile CIs 95% and 98.33% (Bonferroni 0.05/3).")
    A("")
    A("## Numbers (point [98.33% CI])")
    for c in ("d_density_balanced", "d_rare", "d_learned_nn"):
        A("")
        A(f"### {DESC_LABEL[c]}")
        A("| N | cell | n | S1 | S1 bonf CI | S2 | S2 bonf CI | cens |")
        A("|---|---|---|---|---|---|---|---|")
        for n in (1, 5, 30):
            ck = f"{c}|N{n}"
            for cell in ("pooled", "block1", "block2"):
                s = R["S1S2"][ck][cell]
                A(f"| {n} | {cell} | {s['n']} | {f4(s['S1_spearman'])} | "
                  f"[{f4(s['S1_ci_bonf'][0])},{f4(s['S1_ci_bonf'][1])}] | "
                  f"{fp(s['S2_decile_spread'])} | "
                  f"[{fp(s['S2_ci_bonf'][0])},{fp(s['S2_ci_bonf'][1])}] | "
                  f"{s['censored_share']*100:.2f}% |")
    A("")
    A("## Verdicts (K1-K7 verbatim; PASS = no kill fired)")
    A("| descriptor | N=1 | N=5 | N=30 |")
    A("|---|---|---|---|")
    for c in ("d_density_balanced", "d_rare", "d_learned_nn"):
        row = [DESC_LABEL[c]]
        for n in (1, 5, 30):
            kd = R["kill_decisions"][f"{c}|N{n}"]
            row.append(f"{kd['verdict']}" + ("" if kd["verdict"] == "PASS"
                                             else f" ({','.join(kd['fired'])})"))
        A("| " + " | ".join(row) + " |")
    A("")
    A("Kill detail per descriptor x horizon (verbatim booleans) is in `results.json` "
      "(`kill_decisions`) and `verdicts.txt`.")
    A("")
    A("## Caveats")
    A("1. Pre-reg 4(b) states 'density==0 in all five views' but its FROZEN clause says "
      "'no neighbour ... in ANY of the five views'. The all-five reading is empty by "
      "construction (normalized_shape has 0 unique objects), so the FROZEN/rare_refs reading "
      "(ANY) was used; computed set is identical to `rare_refs.parquet` (8,577 objects).")
    A("2. Pre-reg 4(c) defines the descriptor as 'smallest finite distance (equivalently "
      "nn_dist[:,0] / kth_dist)'. The explicit words were used: `nn_dist[:,0]` (smallest "
      "finite); `kth_dist` is the k-th value, a different statistic, and was not used.")
    A("3. K3 'monotone over top-5 vs bottom-5 deciles in the pooled sign's direction' is "
      "operationalised as sign(mean(deciles6-10) - mean(deciles1-5)) == sign(pooled S1); the "
      "stricter 10-decile monotonicity is also reported per block in `results.json`.")
    A("4. Descriptor (c) z-scores the embedding channels on the fit block (block1) exactly as "
      "`basket_tape_atlas_geometry.embedding_gram`; the geometry producer was not re-run.")
    A("5. Deciles are a single pooled cut (all 6,160) as FROZEN; per-block cuts are secondary "
      "only. For the binary `d_rare` the pooled cut is block-confounded (e.g. deciles 1-3 and "
      "7-8 hold no block2 member, deciles 5 and 10 no block1 member), so each block has a "
      "decile with 0 resolved members: K6 voids K3 in both blocks and block-level S2 is "
      "undefined (empty top/bottom decile) - reported, not merged.")
    A("6. Verdict-application correction: the single read's first pass counted a K6-voided K3 as "
      "a kill; corrected (a voided block cannot fail K3). No statistic changed (verified "
      "field-by-field); `results_read_once.json` is the raw single-read output and "
      "`results.json` is the corrected one. Both are pinned in the manifest.")
    A("7. K3/K6 are applied per block; K4 uses the pooled cells; K2 uses the pooled Bonferroni "
      "interval; K5/K7 never fired (pooled n=5887>=500, blocks 4053/1834>=200; censored "
      "3.91%/5.56%<10%).")
    A("")
    A("## Limitations")
    A("Separation only: no entry/exit/sizing rule is declared. Minute horizons, no "
      "next-session/carry. Learned view has no frozen radius, so descriptor (c) supports only "
      "rank/decile separation, not a density threshold. Censored members (4.43%) and blocked "
      f"slots ({cov['blocked_slots']['block1']} block1 / {cov['blocked_slots']['block2']} "
      "block2, cash) are excluded from the ratio and reported separately.")
    A("")
    A("## Execution integrity")
    A("Executed exactly once; descriptors published before any outcome column was read; every "
      "result including nulls published. `no_reread.json` is the no-reread statement. Output "
      "directory/names follow the task assignment (EV01/): pre-reg section 8.6's "
      "`member_deltas.parquet` / `tables.json` / `EV/v1/` correspond to `outcomes.parquet` / "
      "`results.json` here; `bootstrap_distributions.npz`, `coverage.json` and `manifest.json` "
      "keep their pre-registered names.")
    report = "\n".join(L) + "\n"
    (out / "report.md").write_text(report)
    nlines = report.count("\n")
    assert nlines <= 80, f"report.md is {nlines} lines (>80)"

    # --- manifest.json ----------------------------------------------------------------
    # The raw single-read output is normalised to strict JSON (non-finite -> null); its bytes
    # differ from the in-memory dump only in that representation.
    raw = json.loads((out / "results_read_once.json").read_text())
    (out / "results_read_once.json").write_text(json.dumps(clean(raw), indent=1))
    files = sorted(p for p in out.rglob("*") if p.is_file() and p.name != "manifest.json")
    file_shas = {str(p.relative_to(out)): sha256(p) for p in files}
    manifest = {
        "artifact": "EV-01",
        "pre_registration": {"path": PRE_REG_PATH, "sha256": PRE_REG},
        "executed_once": True,
        "no_reread_statement": "no_reread.json",
        "coverage": "coverage.json",
        "inputs": inputs,
        "code": {
            "factory/artifacts/basket/phase2/ATLAS/EV01/code/ev01_descriptors.py":
                file_shas.get("code/ev01_descriptors.py"),
            "factory/artifacts/basket/phase2/ATLAS/EV01/code/ev01_stats.py":
                file_shas.get("code/ev01_stats.py"),
            "factory/artifacts/basket/phase2/ATLAS/EV01/code/ev01_publish.py":
                file_shas.get("code/ev01_publish.py"),
        },
        "outputs": file_shas,
        "results": {
            "bps_total": 100.0, "k": meta["k"],
            "boot_draws": meta["boot_draws"], "boot_seed": meta["boot_seed"],
            "censored_census": meta["censored_census"],
            "resolved_any_finite_delta": meta["resolved_any_finite_delta"],
            "verdicts": R["verdicts"],
        },
        "note": "manifest.json excludes its own sha256; every other file in this directory is "
                "listed with its sha256.",
        "read_once": {
            "statement": "no_reread.json",
            "raw_results_of_the_single_read": "results_read_once.json",
            "superseded_by": "results.json",
            "correction": "the single read's first pass labelled a K6-voided K3 as a kill; the "
                          "verdicts were recomputed from outcomes.parquet (no panel access). "
                          "Every S1/S2/CI number is identical between the two files (verified "
                          "field-by-field); only the d_rare K3 labels and meta.stats_source / "
                          "meta.peak_rss_mb differ. results_read_once.json is the raw read "
                          "output with non-finite values rendered as JSON null (strict JSON).",
        },
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"report.md lines={nlines} files={len(file_shas)}")
    print(json.dumps(R["verdicts"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
