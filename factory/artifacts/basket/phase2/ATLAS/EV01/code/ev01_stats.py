#!/usr/bin/env python3
"""EV-01 outcome/statistics/verdict step. Executes the frozen pre-registration ONCE.

Pre-registration: researches/PRE-REG-TAPE-ATLAS-EV01.md
  sha256 ce61fcaf8ffcb575c3359b2a3801b227fc2e3af8cae9575fa118b5ae0d2e7d85

Normal mode: run the ledger CLI aggregates, read the frozen panel through the ledger
(`replay_member`) to build `outcomes.parquet`, then deciles / S1 / S2 / month-blocked
bootstrap / K1-K7.

`--from-outcomes` mode: rebuild the statistics and the verdicts from an already-published
`outcomes.parquet` only. It NEVER opens the panel or the ledger replay path, so the
outcome-touching read stays executed exactly once; only the (deterministic) statistics and
the K1-K7 application are recomputed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import resource
import subprocess
import sys
from pathlib import Path

import numpy as np
import polars as pl

WT_ROOT = Path("/home/hillel/.config/opencode/worktrees/Alpacatrader/basket-phase2-f1")
DATA_ROOT = Path("/home/hillel/projects/Alpacatrader")

MEMBER_KEYS = ("sleeve_day", "family", "entry_rank", "ticker")
HORIZONS = (1, 5, 30)
DESCRIPTORS = ("d_density_balanced", "d_rare", "d_learned_nn")
BOOT_DRAWS = 10000
BOOT_SEED = 20260922
BONF = 0.05 / 3.0                # Bonferroni over the three primary horizons
ALPHA95 = (0.025, 0.975)
ALPHA_BONF = (BONF / 2.0, 1.0 - BONF / 2.0)   # 98.33% interval


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def rankdata(x: np.ndarray) -> np.ndarray:
    """Average ranks, ties averaged (1-based), fully vectorised."""
    n = x.size
    order = np.argsort(x, kind="mergesort")
    xs = x[order]
    if n == 0:
        return np.empty(0, dtype=np.float64)
    boundaries = np.flatnonzero(xs[1:] != xs[:-1]) + 1
    starts = np.concatenate(([0], boundaries))
    ends = np.concatenate((boundaries, [n]))
    counts = ends - starts
    avg = (starts + ends - 1) / 2.0 + 1.0
    ranks_sorted = np.repeat(avg, counts)
    out = np.empty(n, dtype=np.float64)
    out[order] = ranks_sorted
    return out


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    if x.size < 3:
        return float("nan")
    rx = rankdata(x)
    ry = rankdata(y)
    rx -= rx.mean()
    ry -= ry.mean()
    d = np.sqrt((rx * rx).sum() * (ry * ry).sum())
    return float((rx * ry).sum() / d) if d > 0 else float("nan")


def cell_stats(desc: np.ndarray, delta: np.ndarray, decile: np.ndarray,
               top_mask: np.ndarray, bot_mask: np.ndarray) -> dict:
    s1 = spearman(desc, delta)
    top = delta[top_mask].mean() if top_mask.any() else float("nan")
    bot = delta[bot_mask].mean() if bot_mask.any() else float("nan")
    return {"S1_spearman": s1, "S2_decile_spread": float(top - bot),
            "top_decile_mean": float(top), "bottom_decile_mean": float(bot),
            "n": int(desc.size)}


def boot_cell(desc, delta, decile, months_of_member, n_months, top_mask, bot_mask,
              seed=BOOT_SEED, draws=BOOT_DRAWS):
    """Month-blocked bootstrap: resample months with replacement; all members of a drawn month
    move together. Fresh Generator(seed) per cell (documented scheme)."""
    rng = np.random.default_rng(seed)
    pos_by_month = [np.flatnonzero(months_of_member == m) for m in range(n_months)]
    max_sz = max((len(p) for p in pos_by_month), default=0)
    s1 = np.full(draws, np.nan)
    s2 = np.full(draws, np.nan)
    if max_sz == 0:
        return s1, s2
    padded = np.full((n_months, max_sz), -1, dtype=np.int64)
    for m, p in enumerate(pos_by_month):
        if len(p):
            padded[m, :len(p)] = p
    pick = rng.integers(0, n_months, size=(draws, n_months))
    for d in range(draws):
        flat = padded[pick[d]].ravel()
        idx = flat[flat >= 0]
        if idx.size == 0:
            continue
        s1[d] = spearman(desc[idx], delta[idx])
        tm = top_mask[idx]
        bm = bot_mask[idx]
        if tm.any() and bm.any():
            s2[d] = delta[idx][tm].mean() - delta[idx][bm].mean()
    return s1, s2


def pct(v, lo, hi):
    if not np.isfinite(v).any():
        return [float("nan"), float("nan")]
    return [float(np.nanpercentile(v, lo * 100)), float(np.nanpercentile(v, hi * 100))]


def clean(o):
    """Recursively map non-finite floats to None so results.json is strict JSON."""
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [clean(v) for v in o]
    if isinstance(o, (bool, np.bool_)):
        return bool(o)
    if isinstance(o, (int, np.integer)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        f = float(o)
        return None if not math.isfinite(f) else f
    return o


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wt-root", type=Path, default=WT_ROOT)
    ap.add_argument("--data-root", type=Path, default=DATA_ROOT)
    ap.add_argument("--out", type=Path,
                    default=WT_ROOT / "factory/artifacts/basket/phase2/ATLAS/EV01")
    ap.add_argument("--from-outcomes", type=Path, default=None,
                    help="recompute statistics/verdicts from a published outcomes.parquet "
                         "without opening the panel (never a second outcome read)")
    args = ap.parse_args(argv)
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    (out / "logs").mkdir(exist_ok=True)

    panel = args.wt_root / "factory/artifacts/basket/phase2/ATLAS/panel.parquet"
    dpath = out / "descriptors.parquet"
    assert dpath.exists(), "descriptors.parquet must be published before any outcome read"
    desc_sha = sha256(dpath)

    sys.path.insert(0, str(args.wt_root / "factory/scripts"))
    import basket_atlas_ledger as L
    k = L.fric_k(100.0)

    if args.from_outcomes is None:
        # ---- phase A: ledger CLI aggregates (pre-reg section 8.2) --------------------
        ledger_jsons = {}
        specs = [("hold_flat", "hold")] + [(f"exit_at_bar:{n}", f"exit{n}") for n in HORIZONS]
        for spec, tag in specs:
            jpath = out / f"ledger_{tag}.json"
            log = out / "logs" / f"ledger_{tag}.log"
            cmd = [sys.executable, str(args.wt_root / "factory/scripts/basket_atlas_ledger.py"),
                   "--rule", spec, "--panel", str(panel), "--bps", "100.0", "--json", str(jpath)]
            p = subprocess.run(cmd, capture_output=True, text=True)
            log.write_text(p.stdout + "\n--- stderr ---\n" + p.stderr)
            assert p.returncode == 0, f"ledger CLI {spec} rc={p.returncode}: {p.stderr[:500]}"
            ledger_jsons[tag] = (spec, jpath, sha256(jpath))

        # ---- phase B: per-member deltas via the ledger --------------------------------
        reg = L.load_registry()
        rule30 = L.parse_rule("exit_at_bar:30", reg)
        df = L.load_panel(panel, rule30)          # exit_at_bar:N share one column set
        rules = {n: L.parse_rule(f"exit_at_bar:{n}", reg) for n in HORIZONS}
        hold_rule = L.parse_rule("hold_flat", reg)

        rows = []
        for key, sub in L.members_of(df):
            r0 = L.replay_member(sub, hold_rule, k)
            row = {"sleeve_day": r0["sleeve_day"], "family": r0["family"],
                   "entry_rank": r0["entry_rank"], "ticker": r0["ticker"],
                   "block": r0["block"], "month": str(sub["month"][0]),
                   "entry_et": r0["entry_et"], "session_end": r0["session_end"],
                   "n_bars": r0["n_bars"], "terminal_censored": bool(r0["terminal_censored"]),
                   "hold_gross": r0.get("gross_rule"), "hold_net": r0.get("net_rule")}
            row["member_id"] = (f"{row['sleeve_day']}|{row['family']}|{row['ticker']}|"
                                f"{row['entry_et']}|{row['entry_rank']}")
            for n in HORIZONS:
                r = L.replay_member(sub, rules[n], k)
                row[f"delta_{n}"] = r.get("delta")
                row[f"status_{n}"] = r.get("status")
            rows.append(row)
        del df
        out_tbl = pl.DataFrame(rows)
        assert out_tbl.height == 6160, out_tbl.height

        desc = pl.read_parquet(dpath)
        joined = out_tbl.join(desc, on="member_id", how="left", suffix="_d")
        assert joined.height == 6160
        for c in ("block", "month"):
            assert joined[f"{c}_d"].null_count() == 0
            assert (joined[c] == joined[f"{c}_d"]).all()
            joined = joined.drop(f"{c}_d")
        for c in DESCRIPTORS:
            assert joined[c].null_count() == 0, c
        mem = pl.read_parquet(args.data_root / "data/atlas/observation/v0/memberships.parquet")
        assert set(joined["member_id"].to_list()) == set(mem["member_id"].to_list())

        jpath = out / "outcomes.parquet"
        joined.select(["member_id", *MEMBER_KEYS, "path_id", "block", "month", "entry_et",
                       "session_end", "n_bars", "terminal_censored", "hold_gross", "hold_net",
                       *[f"delta_{n}" for n in HORIZONS],
                       *[f"status_{n}" for n in HORIZONS], *DESCRIPTORS]).write_parquet(jpath)
        out_sha = sha256(jpath)
        stats_source = "panel read once through the ledger"
    else:
        joined = pl.read_parquet(args.from_outcomes)
        out_sha = sha256(args.from_outcomes)
        stats_source = f"recomputed from {args.from_outcomes.name} (no panel read)"
        assert joined.height == 6160

    # ---- phase C: pooled block-tagged deciles ---------------------------------------
    B = joined["block"].to_numpy()
    MON = joined["month"].to_numpy()
    CEN = joined["terminal_censored"].to_numpy()
    member_ids = joined["member_id"].to_numpy()
    decile_cols = {}
    decile_block_counts = {}
    for c in DESCRIPTORS:
        v = joined[c].to_numpy().astype(np.float64)
        # deterministic order: (descriptor, member_id); NaN would sort last
        order = np.lexsort((member_ids, v))
        dec = np.empty(6160, dtype=np.int64)
        dec[order] = np.arange(6160) // 616 + 1
        decile_cols[c] = dec
        decile_block_counts[c] = {
            int(d): {"block1": int(((dec == d) & (B == "block1")).sum()),
                     "block2": int(((dec == d) & (B == "block2")).sum())}
            for d in range(1, 11)}

    # ---- phase D: statistics + bootstrap ---------------------------------------------
    results = {"S1S2": {}, "deciles": {}, "kill_decisions": {}, "verdicts": {}}
    boot_arrays = {}
    sign = lambda z: 0 if (z is None or not np.isfinite(z) or z == 0) else (1 if z > 0 else -1)
    for c in DESCRIPTORS:
        dv = joined[c].to_numpy().astype(np.float64)
        dec = decile_cols[c]
        results["deciles"][c] = decile_block_counts[c]
        for n in HORIZONS:
            dl = joined[f"delta_{n}"].cast(pl.Float64).to_numpy()
            resolved = np.isfinite(dl) & np.isfinite(dv)
            cell_key = f"{c}|N{n}"
            results["S1S2"][cell_key] = {}
            block_cell = {}
            for cell in ("block1", "block2"):
                sel = resolved & (B == cell)
                idx = np.flatnonzero(sel)
                mons = sorted(set(MON[idx]))
                m2i = {m: i for i, m in enumerate(mons)}
                mm = np.array([m2i[m] for m in MON[idx]])
                top = (dec[idx] == 10)
                bot = (dec[idx] == 1)
                st = cell_stats(dv[idx], dl[idx], dec[idx], top, bot)
                st["n_months"] = len(mons)
                st["n_censored"] = int(((B == cell) & CEN).sum())
                st["n_members_block"] = int((B == cell).sum())
                st["censored_share"] = st["n_censored"] / st["n_members_block"]
                if st["n"] >= 30:
                    s1, s2 = boot_cell(dv[idx], dl[idx], dec[idx], mm, len(mons), top, bot)
                    boot_arrays[f"{cell_key}|{cell}|S1"] = s1
                    boot_arrays[f"{cell_key}|{cell}|S2"] = s2
                    st["S1_ci95"] = pct(s1, *ALPHA95)
                    st["S1_ci_bonf"] = pct(s1, *ALPHA_BONF)
                    st["S2_ci95"] = pct(s2, *ALPHA95)
                    st["S2_ci_bonf"] = pct(s2, *ALPHA_BONF)
                    st["S1_boot_mean"] = float(np.nanmean(s1)) if np.isfinite(s1).any() else None
                    st["S2_boot_mean"] = float(np.nanmean(s2)) if np.isfinite(s2).any() else None
                results["S1S2"][cell_key][cell] = st
                block_cell[cell] = st
            # pooled
            idx = np.flatnonzero(resolved)
            mons = sorted(set(MON[idx]))
            m2i = {m: i for i, m in enumerate(mons)}
            mm = np.array([m2i[m] for m in MON[idx]])
            top = (dec[idx] == 10)
            bot = (dec[idx] == 1)
            st = cell_stats(dv[idx], dl[idx], dec[idx], top, bot)
            st["n_months"] = len(mons)
            st["n_censored"] = int(CEN.sum())
            st["n_members_block"] = 6160
            st["censored_share"] = st["n_censored"] / 6160
            s1, s2 = boot_cell(dv[idx], dl[idx], dec[idx], mm, len(mons), top, bot)
            boot_arrays[f"{cell_key}|pooled|S1"] = s1
            boot_arrays[f"{cell_key}|pooled|S2"] = s2
            st["S1_ci95"] = pct(s1, *ALPHA95)
            st["S1_ci_bonf"] = pct(s1, *ALPHA_BONF)
            st["S2_ci95"] = pct(s2, *ALPHA95)
            st["S2_ci_bonf"] = pct(s2, *ALPHA_BONF)
            st["S1_boot_mean"] = float(np.nanmean(s1)) if np.isfinite(s1).any() else None
            st["S2_boot_mean"] = float(np.nanmean(s2)) if np.isfinite(s2).any() else None
            results["S1S2"][cell_key]["pooled"] = st

            # decile means per block (for K3/K6) and pooled
            dm = {}
            for cell in ("block1", "block2", "pooled"):
                sel = resolved & ((B == cell) if cell != "pooled" else np.ones(6160, bool))
                means, counts = [], []
                for d in range(1, 11):
                    m = sel & (dec == d)
                    means.append(float(dl[m].mean()) if m.any() else float("nan"))
                    counts.append(int(m.sum()))
                dm[cell] = {"decile_means": means, "decile_counts": counts}
            block_cell["_decile_means"] = dm

            # ---- K1--K7 verbatim ------------------------------------------------------
            s1_b1 = block_cell["block1"]["S1_spearman"]
            s1_b2 = block_cell["block2"]["S1_spearman"]
            s1_pool = st["S1_spearman"]
            k1 = (sign(s1_pool) != 0 and sign(s1_pool) == sign(s1_b1) == sign(s1_b2))
            k2_ci = st["S1_ci_bonf"]
            k2 = bool(not (k2_ci[0] <= 0.0 <= k2_ci[1]))   # interval must EXCLUDE 0
            k3 = {}
            for cell in ("block1", "block2"):
                dmeans = dm[cell]["decile_means"]
                dcounts = dm[cell]["decile_counts"]
                if min(dcounts) < 30:                   # K6 voids K3 for this block
                    k3[cell] = {"ok": None, "voided_by_K6": True,
                                "min_decile_count": min(dcounts)}
                    continue
                top5 = float(np.nanmean(dmeans[5:]))
                bot5 = float(np.nanmean(dmeans[:5]))
                ok = (sign(top5 - bot5) == sign(s1_pool)) if sign(top5 - bot5) != 0 else False
                strict = all(dmeans[i] <= dmeans[i + 1] for i in range(9)) if sign(s1_pool) > 0 \
                    else all(dmeans[i] >= dmeans[i + 1] for i in range(9))
                k3[cell] = {"ok": bool(ok), "voided_by_K6": False, "top5_mean": top5,
                            "bottom5_mean": bot5, "min_decile_count": min(dcounts),
                            "strict_decile_monotone": bool(strict)}
            # K6-voided blocks cannot fail K3: only an evaluable block whose direction is wrong fires.
            k3_fires = any(v.get("ok") is False for v in k3.values())
            k3_voided = [cell for cell, v in k3.items() if v.get("voided_by_K6")]
            k4 = (abs(s1_pool) >= 0.02) and (abs(st["S2_decile_spread"]) >= 0.01)
            n_b1 = block_cell["block1"]["n"]
            n_b2 = block_cell["block2"]["n"]
            k5_pooled_drop = st["n"] < 500
            k5_report_only = [b for b, nn in (("block1", n_b1), ("block2", n_b2)) if nn < 200]
            k6 = {cell: (min(dm[cell]["decile_counts"]) < 30) for cell in ("block1", "block2")}
            k7 = {cell: bool(block_cell[cell]["censored_share"] > 0.10)
                  for cell in ("block1", "block2")}
            fired = []
            if not k1:
                fired.append("K1")
            if not k2:
                fired.append("K2")
            if k3_fires:
                fired.append("K3")
            if not k4:
                fired.append("K4")
            if k5_pooled_drop:
                fired.append("K5(pooled<500)")
            if any(k7.values()):
                fired.append("K7")
            kills = {"K1_sign_stability": bool(k1),
                     "K1_signs": {"pooled": sign(s1_pool), "block1": sign(s1_b1),
                                  "block2": sign(s1_b2)},
                     "K2_bonf_ci_excludes_zero": k2, "K2_ci_bonf": k2_ci,
                     "K3_fires": bool(k3_fires),
                     "K3_monotone_top5_vs_bottom5": bool(not k3_fires),
                     "K3_detail": k3, "K3_voided_blocks": k3_voided,
                     "K4_effect_floor": bool(k4),
                     "K4_abs_spearman": abs(s1_pool),
                     "K4_decile_spread": abs(st["S2_decile_spread"]),
                     "K5_pooled_n": st["n"], "K5_block_n": {"block1": n_b1, "block2": n_b2},
                     "K5_pooled_dropped": bool(k5_pooled_drop),
                     "K5_report_only_blocks": k5_report_only,
                     "K6_empty_decile": k6, "K7_coverage": k7,
                     "fired": fired,
                     "verdict": "DISMISS" if fired else "PASS"}
            results["kill_decisions"][cell_key] = kills
            results["verdicts"][cell_key] = kills["verdict"]

    results["meta"] = {
        "pre_registration_sha256": sha256(
            args.wt_root / "researches/PRE-REG-TAPE-ATLAS-EV01.md"),
        "descriptors_sha256": desc_sha,
        "outcomes_sha256": out_sha,
        "panel_sha256": sha256(panel),
        "bps_total": 100.0, "k": k,
        "boot_draws": BOOT_DRAWS, "boot_seed": BOOT_SEED,
        "bonferroni_alpha": BONF,
        "stats_source": stats_source,
        "decile_rule": "pooled over all 6,160 members, equal-count bins (616 each) on "
                       "(descriptor, member_id); block-tagged",
        "censored_census": {
            "block1": int(((B == "block1") & CEN).sum()),
            "block2": int(((B == "block2") & CEN).sum()),
            "total": int(CEN.sum())},
        "resolved_any_finite_delta": {f"N{n}": int(np.isfinite(
            joined[f"delta_{n}"].cast(pl.Float64).to_numpy()).sum()) for n in HORIZONS},
        "peak_rss_mb": peak_rss_mb(),
    }

    (out / "results.json").write_text(json.dumps(clean(results), indent=1))
    np.savez_compressed(out / "bootstrap_distributions.npz", **boot_arrays)
    if args.from_outcomes is None:
        (out / "outcomes.sha256").write_text(out_sha + "  outcomes.parquet\n")
        (out / "no_reread.json").write_text(json.dumps({
            "pre_registration_sha256": results["meta"]["pre_registration_sha256"],
            "executed_once": True,
            "statement": ("The outcome-touching read of PRE-REG-TAPE-ATLAS-EV01 was executed "
                          "exactly once. No horizon re-selection, no re-run, no peek before the "
                          "descriptor freeze; every result including nulls is published. "
                          "Descriptors were published (descriptors.parquet, sha "
                          f"{desc_sha}) before this step opened the panel. Statistics after the "
                          "read are recomputed deterministically from the published "
                          "outcomes.parquet (that file is never rewritten)."),
            "n_bootstrap_draws": BOOT_DRAWS, "seed": BOOT_SEED,
        }, indent=1))

    # text ledger of kill decisions (verbatim)
    lines = ["# EV-01 verdicts (K1..K7 verbatim apply)", ""]
    for cell_key, kd in results["kill_decisions"].items():
        lines.append(f"{cell_key}: {kd['verdict']}  fired={kd['fired']}")
        lines.append(f"    S1_pooled={results['S1S2'][cell_key]['pooled']['S1_spearman']:.6f} "
                     f"bonf_ci={kd['K2_ci_bonf']} S2_spread="
                     f"{results['S1S2'][cell_key]['pooled']['S2_decile_spread']:.6f}"
                     + (f"  K3_voided={kd['K3_voided_blocks']}" if kd.get("K3_voided_blocks")
                        else ""))
    (out / "verdicts.txt").write_text("\n".join(lines) + "\n")

    print(f"outcomes.parquet sha256={out_sha}")
    print(f"peak_rss_mb={results['meta']['peak_rss_mb']:.1f}")
    for cell_key, v in results["verdicts"].items():
        print(f"  {cell_key}: {v}  fired={results['kill_decisions'][cell_key]['fired']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
