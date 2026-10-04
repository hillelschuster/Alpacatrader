#!/usr/bin/env python3
"""Compact deterministic reader for the four owned-claim lifecycle dollar questions.

Reads the two portfolio artifacts plus the current dollar surfaces and answers, with
predefined business comparisons only (no search over the published cell grid):

  1. absorbed damage vs terminal decay
  2. repair vs failed-repair cells
  3. retaining to a push vs immediate sale
  4. fresh reentry vs cash

N3 and N5, and the four admission clocks, are kept separate everywhere. Units and
mean bases are preserved as published: surface increments are dollars per the
original claim cash budget (reported per $100) with day-balanced means; policy
returns are portfolio returns of the original equal-dollar book (reported as
percent). Independent day counts, event counts, UNKNOWN residuals and day standard
errors are carried through unchanged; no significance test is invented. Bar and
open-price proxies are descriptive rulers, never deployable signals, and the
producers' model-evidence flag is not a strategy flag.

Every fixed horizon (15/30/60/120) is reported independently as a signed
counterfactual owned-dollar reading with its own support counts. The reader never
prefers or ranks a horizon, never derives a strategy verdict from a fixed-horizon
average, never backfills one horizon from another, and invents no significance
test. It consumes only already-published JSON, never reads protected outcomes,
never runs a model, and hard-fails when a mandatory input is absent rather than
substituting a placeholder or an older result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

HORIZONS = (15, 30, 60, 120)
VIEWS = (3, 5)
CLOCKS = (540, 560, 569, 571)
PER_100 = 100.0
SLICE_KEYS = ("episode", "tod", "entry_level", "repair_state", "flow_state", "damage_history")

SURFACES_NAME = "owned_claim_surfaces.json"
POLICY_BASELINE_NAME = "owned_claim_policy_discovery.json"
POLICY_FULL_NAME = "owned_claim_policy_full_discovery.json"
FINDINGS_NAME = "owned_claim_findings.json"

SURFACES_REQUIRED = (
    "kind",
    "days",
    "source_sha256",
    "event_manifest_sha256",
    "units",
    "sampling",
    "caveats",
    "states",
    "reentry",
)
POLICY_REQUIRED = (
    "kind",
    "source_sha256",
    "test_days",
    "caveats",
    "summary",
    "folds",
    "paired",
    "timing",
)
POLICY_SECTIONS = {
    "summary": ("clock", "n", "side", "policy", "ev", "known_days"),
    "folds": ("fold", "clock", "n", "side", "policy", "ev"),
    "paired": ("clock", "n", "side", "policy", "paired_days", "delta_stop", "reference"),
    "timing": ("clock", "n", "side", "policy", "median_exit_et", "sales_before_13_share"),
}

# Predefined comparisons. Each entry is an explicit published cell path; the reader
# never scans or ranks the 7216-cell surface grid.
DEFINED_CONTRASTS = {
    "Q1_absorbed_damage_vs_terminal_decay": [
        (
            "surfaces",
            "damage",
            {
                "entry_level": "below_entry",
                "repair_state": "not_repairing",
                "flow_state": "not_expanding",
            },
            "damage_thesis_terminal",
        ),
        (
            "surfaces",
            "damage",
            {
                "entry_level": "below_entry",
                "repair_state": "repairing",
                "flow_state": "expanding_flow",
            },
            "damage_thesis_absorbed",
        ),
        (
            "surfaces",
            "flush",
            {
                "entry_level": "below_entry",
                "repair_state": "not_repairing",
                "flow_state": "not_expanding",
            },
            "flush_terminal",
        ),
        (
            "surfaces",
            "flush",
            {
                "entry_level": "below_entry",
                "repair_state": "repairing",
                "flow_state": "expanding_flow",
            },
            "flush_absorbed",
        ),
        (
            "surfaces",
            "stall",
            {
                "entry_level": "below_entry",
                "repair_state": "not_repairing",
                "flow_state": "not_expanding",
            },
            "stall_terminal",
        ),
        (
            "surfaces",
            "stall",
            {
                "entry_level": "below_entry",
                "repair_state": "repairing",
                "flow_state": "expanding_flow",
            },
            "stall_absorbed",
        ),
        ("surfaces", "damage", {"episode": "first", "tod": "10_11"}, "damage_first_midmorning"),
        ("surfaces", "damage", {"episode": "repeat", "tod": "10_11"}, "damage_repeat_midmorning"),
    ],
    "Q2_genuine_vs_fake_repair": [
        (
            "surfaces",
            "repair",
            {
                "entry_level": "below_entry",
                "repair_state": "repairing",
                "flow_state": "expanding_flow",
            },
            "genuine_repair_below_flow",
        ),
        (
            "surfaces",
            "repair",
            {
                "entry_level": "above_entry",
                "repair_state": "repairing",
                "flow_state": "expanding_flow",
            },
            "genuine_repair_above_flow",
        ),
        (
            "surfaces",
            "failed_repair",
            {
                "entry_level": "below_entry",
                "repair_state": "not_repairing",
                "flow_state": "not_expanding",
            },
            "fake_repair_below_noflow",
        ),
        (
            "surfaces",
            "failed_repair",
            {
                "entry_level": "below_entry",
                "repair_state": "not_repairing",
                "flow_state": "expanding_flow",
            },
            "fake_repair_below_flow",
        ),
        ("surfaces", "repair", {"episode": "first", "tod": "10_11"}, "genuine_repair_first"),
        ("surfaces", "repair", {"episode": "repeat", "tod": "10_11"}, "genuine_repair_repeat"),
        ("surfaces", "failed_repair", {"episode": "first", "tod": "10_11"}, "fake_repair_first"),
        ("surfaces", "failed_repair", {"episode": "repeat", "tod": "10_11"}, "fake_repair_repeat"),
    ],
    "Q3_monetize_push_vs_retain_optionality": [
        ("surfaces", "profit5", {"damage_history": "prior_damage"}, "damaged_push_first_profit5"),
        ("surfaces", "profit5", {"damage_history": "no_prior_damage"}, "clean_push_first_profit5"),
        (
            "surfaces",
            "new_high",
            {
                "entry_level": "above_entry",
                "repair_state": "not_repairing",
                "flow_state": "expanding_flow",
            },
            "clean_push_new_high_flow",
        ),
        (
            "surfaces",
            "new_high",
            {
                "entry_level": "below_entry",
                "repair_state": "repairing",
                "flow_state": "expanding_flow",
            },
            "recovery_push_new_high_flow",
        ),
        (
            "surfaces",
            "giveback",
            {
                "entry_level": "above_entry",
                "repair_state": "repairing",
                "flow_state": "not_expanding",
            },
            "giveback_retain_cost",
        ),
        (
            "surfaces",
            "profit5",
            {"episode": "first", "tod": "10_11"},
            "clean_push_first_midmorning",
        ),
        ("surfaces", "profit5", {"episode": "first", "tod": "11_13"}, "clean_push_first_11_13"),
        ("surfaces", "profit5", {"episode": "first", "tod": "after13"}, "clean_push_first_after13"),
    ],
    "Q4_reentry_vs_cash": [
        (
            "reentry",
            "repair",
            {"entry_level": "below_entry", "flow_state": "expanding_flow"},
            "reentry_repair_below_flow",
        ),
        (
            "reentry",
            "repair",
            {"entry_level": "above_entry", "flow_state": "expanding_flow"},
            "reentry_repair_above_flow",
        ),
        (
            "reentry",
            "rank_recovery",
            {"entry_level": "above_entry", "flow_state": "expanding_flow"},
            "reentry_rank_recovery_above_flow",
        ),
        (
            "reentry",
            "reclaim",
            {"entry_level": "above_entry", "flow_state": "expanding_flow"},
            "reentry_reclaim_above_flow",
        ),
        (
            "reentry",
            "resurrection",
            {"entry_level": "above_entry", "flow_state": "expanding_flow"},
            "reentry_resurrection_above_flow",
        ),
        (
            "reentry",
            "new_high",
            {"entry_level": "above_entry", "flow_state": "expanding_flow"},
            "reentry_new_high_above_flow",
        ),
    ],
}

# Retained-baseline lineage declared by owned_claim_contract.json
# (training_contract.controlled_refinement: baseline source/results preserved at
# commit76db4a1). It is a declaration carried from the contract. No hash-to-commit
# registry is published, so no observed hash is ever mapped to this commit and no
# input is asserted to be this source.
BASELINE_SOURCE_COMMIT = "76db4a1"

# Every horizon is an independent signed counterfactual dollar reading. A positive
# value is never promoted to an executable strategy, an identification of the true
# exit route, or a significance result.
HORIZON_MEANING = (
    "Each horizon is reported on its own: the day-balanced mean incremental dollars "
    "per $100 of the original claim cash budget of retaining to that fixed horizon "
    "versus selling at the decision (immediate sale = 0 reference). A positive value "
    "is a counterfactual owned-dollar increment under the modeled fixed-share, "
    "common-exit-fee ruler. It is NOT an executable policy return, not a "
    "significance result, and does not identify the true exit route. A negative "
    "fixed-terminal value does not by itself prove that earlier release or later "
    "retention was optimal, that retained option value was absent, or that the "
    "roster failed. All four horizons stay visible together with their support."
)

PER_HORIZON_DESCRIPTION = (
    "signed counterfactual owned-dollar increment per $100 of original claim "
    "capital versus immediate sale at this fixed horizon, reported independently of "
    "the other horizons"
)

QUESTION_SEMANTICS = {
    "Q1_absorbed_damage_vs_terminal_decay": (
        "Descriptive signed-dollar contrast between published joint-state cells; no decay or "
        "recovery verdict is derived from any single horizon."
    ),
    "Q2_genuine_vs_fake_repair": (
        "Descriptive signed-dollar contrast between published repair and failed-repair cells; "
        "no genuine/fake or causal strategy support label is assigned."
    ),
    "Q3_monetize_push_vs_retain_optionality": (
        "Per fixed horizon, whether retaining to a push carries positive counterfactual "
        "owned-dollar value versus immediate sale. A horizon average does not establish that "
        "monetizing is universally better or that option value is absent."
    ),
    "Q4_reentry_vs_cash": (
        "Per fixed horizon, descriptive signed-dollar contrast of fresh reentry versus cash "
        "(0 reference). No universal claim that reentry beats cash, or that cash beats all "
        "reentry, is made."
    ),
}

# Clean push vs damaged recovery maturity: side-by-side published cells.
MATURITY_CELLS = [
    ("surfaces", "profit5", {"damage_history": "no_prior_damage"}, "clean_push"),
    (
        "surfaces",
        "new_high",
        {
            "entry_level": "above_entry",
            "repair_state": "not_repairing",
            "flow_state": "expanding_flow",
        },
        "clean_push",
    ),
    ("surfaces", "profit5", {"damage_history": "prior_damage"}, "damaged_recovery"),
    (
        "surfaces",
        "damage",
        {"entry_level": "below_entry", "repair_state": "repairing", "flow_state": "expanding_flow"},
        "damaged_recovery",
    ),
]

METHOD_RATIONALE = (
    "Baseline labels (owned_claim_policy_discovery.json) use the iteration-0 "
    "full-session-hold initialization ruler. The full-discovery refinement "
    "(owned_claim_policy_full_discovery.json) labels each decision by waiting one "
    "strictly later executable event and then realizing the COMPLETE previously "
    "learned stopping policy; after the initialization ruler, later labels use "
    "adaptive learned exits with the same features, weights, parameters, folds and "
    "views. Differences are label-policy differences only: no new selection, "
    "threshold grid, clipping or tuning. Both artifacts remain discovery-only "
    "out-of-fold portfolio proxies, not validated edges. This reader re-derives "
    "and validates neither label method, so both families stay unvalidated "
    "discovery evidence regardless of which source lineage their artifacts "
    "declare: a version, commit or hash declaration is not a validation."
)

LABEL_METHODS = {
    "baseline": {
        "artifact": POLICY_BASELINE_NAME,
        "label_method": "fitted_value_iteration_one_step_control",
        "declared_support": (
            "one-step fitted-value target y=(next_sell_px-sell_px)/original_fill_px + "
            "max(0,q_pred_at_next_event) with terminal nextq=0; the FVI control retains its "
            "declared one-step support and is not a complete-policy roll-out."
        ),
    },
    "full_discovery": {
        "artifact": POLICY_FULL_NAME,
        "label_method": "controlled_refinement_wait_then_complete_policy",
        "declared_support": (
            "declared target (future policy liquidation - current liquidation)/original_fill "
            "after waiting one strictly later executable event and then realizing the complete "
            "previously learned stopping policy."
        ),
    },
    "distinction": (
        "Two distinct label methods that must never be read as the same rule. Metadata "
        "distinguishing them is carried per family; no run is labelled corrected or "
        "verified from a version, commit or hash declaration."
    ),
}

FIXED_CAVEATS = [
    (
        "N3 and N5 are independent ownership/candidate universes and are never pooled; every "
        "view and every admission clock is reported separately."
    ),
    (
        "Pooled or cross-cell descriptive surface readings are rulers, never validation: they "
        "are not confirmatory and not out-of-sample evidence."
    ),
    (
        "Paired policy deltas use joint known days only; unpaired surface cells describe "
        "joint-state dollars with no date matching. The two must never be read as each other."
    ),
    (
        "Calendar is exact, not inferred. The surface panel is exactly 533 explicitly "
        "enumerated discovery dates ending 2023-03-14 and the policy test panel is 383 "
        "chronological dates over the same span; there is no 531-day total, no non-contiguous "
        "or calendar-hole claim, and no imputed day. Known-outcome support varies by cell and "
        "horizon and may be fewer than the date count: per-cell known_days/known_events/"
        "unknown_events are reported as published and are never filled with zero."
    ),
    (
        "This reader performs no new protected read. The verified protected lifecycle boundary "
        "and any earlier global-calendar (HARVEST01) economic inspection are separate facts "
        "owned by their own artifacts; they are not asserted, merged or re-derived here."
    ),
    (
        "Each fixed horizon (15/30/60/120) is an independent signed counterfactual owned-dollar "
        "reading vs immediate sale (0 reference). No horizon is preferred, ranked or backfilled "
        "from another, and a positive value is not an executable policy return, not a "
        "significance result, and not identification of the true exit route. A negative "
        "fixed-terminal value does not by itself prove that earlier release or later retention "
        "was optimal, that retained option value was absent, or that the roster failed."
    ),
    (
        "New-buy quantities are fixed from the causal completed mark at 90% of reserved settled "
        "budget, with no resizing, leverage or gap financing; price-gap funding failure stays "
        "UNKNOWN."
    ),
    (
        "Session end / terminal liquidation is a disclosed accounting boundary ruler, not a "
        "strategy; no end-of-day objective is optimized."
    ),
    (
        "No winner-classification or accuracy objective is used; the surfaces are joint-state "
        "dollar readings of owned-share stopping."
    ),
    (
        "Units: surface increments are dollars per the original claim cash budget with entry "
        "cost sunk and a common modeled exit fee, reported per $100 of original claim capital; "
        "policy returns are portfolio returns of the original equal-dollar book, reported as "
        "percent. The two are not the same weighting."
    ),
    (
        "Mean basis: surface cells are day-balanced (mean of per-day means); policy cells are "
        "day means of portfolio returns. Claim-balanced (event-weighted) per-cell means are "
        "NOT published by either artifact, so they are not reported here; known_days and "
        "known_events are carried instead."
    ),
    (
        "UNKNOWN outcome, execution and price-gap funding failures remain null with their event "
        "counts; they are never filled with zero."
    ),
    (
        "No significance test is invented. day_se and independent known_days are reported "
        "exactly as published."
    ),
    (
        "The producer model_evidence_flag is a self-check, not a strategy flag. Bar/open-price "
        "proxies are not quote- or queue-certified and are not deployable signals."
    ),
    "Discovery-only: no protected second-half outcomes, no freeze, no deployment.",
]


# --------------------------------------------------------------------------- utils


def _finite(x):
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def per100(x):
    v = _finite(x)
    return None if v is None else round(PER_100 * v, 6)


def sha256_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path, name):
    if not path.is_file():
        raise SystemExit(f"owned_claim_findings: mandatory input missing: {name} at {path}")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"owned_claim_findings: unreadable input {name} at {path}: {exc}") from exc


def require_keys(obj, keys, name):
    missing = [k for k in keys if k not in obj]
    if missing:
        raise SystemExit(f"owned_claim_findings: {name} lacks required field(s): {missing}")


def slice_key(row):
    return tuple(sorted((k, row[k]) for k in SLICE_KEYS if k in row))


def row_key(row):
    return (row["n"], row["clock"], row["family"], row["outcome"], slice_key(row))


def index_rows(rows, name):
    out = {}
    for r in rows:
        try:
            k = row_key(r)
        except KeyError as exc:
            raise SystemExit(f"owned_claim_findings: {name} record missing key {exc}") from exc
        if k in out:
            raise SystemExit(f"owned_claim_findings: duplicate cell in {name}: {k}")
        out[k] = r
    return out


def index_by(rows, keys, name):
    out = {}
    for r in rows:
        try:
            k = tuple(r[x] for x in keys)
        except KeyError as exc:
            raise SystemExit(f"owned_claim_findings: {name} record missing key {exc}") from exc
        if k in out:
            raise SystemExit(f"owned_claim_findings: duplicate row in {name}: {k}")
        out[k] = r
    return out


def find_cell(index, n, clock, family, outcome, sl):
    return index.get((n, clock, family, outcome, tuple(sorted(sl.items()))))


def direction_of(mean_per_100):
    if mean_per_100 is None:
        return "unknown"
    if mean_per_100 > 0:
        return "positive_increment"
    if mean_per_100 < 0:
        return "negative_increment"
    return "zero_increment"


def support_flags(cell):
    kd = cell["known_days"]
    ke = cell["known_events"]
    se = cell["selected_events"]
    ue = cell["unknown_events"]
    if kd <= 0 or ke <= 0:
        status = "none"
    elif ue > 0:
        status = "partial"
    else:
        status = "complete"
    share = None if se <= 0 else round(ke / se, 6)
    return {
        "support_status": status,
        "known_days": kd,
        "known_events": ke,
        "unknown_events": ue,
        "selected_events": se,
        "known_event_share": share,
    }


def horizon_block(index, n, clock, family, sl, outcome_fn):
    block = {}
    for h in HORIZONS:
        r = find_cell(index, n, clock, family, outcome_fn(h), sl)
        if r is None:
            # An unpublished cell is not a zero; it stays null and unpublished.
            cell = {
                "published": False,
                "mean_per_100": None,
                "median_day_per_100": None,
                "day_se_per_100": None,
                "known_days": 0,
                "known_events": 0,
                "selected_events": 0,
                "unknown_events": 0,
            }
        else:
            kd = int(r.get("known_days") or 0)
            ke = int(r.get("known_events") or 0)
            se = int(r.get("selected_events") or 0)
            cell = {
                "published": True,
                "mean_per_100": per100(r.get("mean_increment")),
                "median_day_per_100": per100(r.get("median_day_increment")),
                "day_se_per_100": per100(r.get("day_se")),
                "known_days": kd,
                "known_events": ke,
                "selected_events": se,
                "unknown_events": max(0, se - ke),
            }
        cell["direction"] = direction_of(cell["mean_per_100"])
        cell["support"] = support_flags(cell)
        cell["description"] = PER_HORIZON_DESCRIPTION
        block[str(h)] = cell
    return block


def slope_120_minus_15(block):
    a = block[str(15)]["mean_per_100"]
    b = block[str(120)]["mean_per_100"]
    if a is None or b is None:
        # No fallback to other horizons: a missing 15 or 120 is reported missing.
        return None
    return round(b - a, 6)


def temporal_contrasts(block):
    out = {}
    for a, b in ((15, 30), (30, 60), (60, 120), (15, 120)):
        va = block[str(a)]["mean_per_100"]
        vb = block[str(b)]["mean_per_100"]
        out[f"{b}_minus_{a}"] = None if va is None or vb is None else round(vb - va, 6)
    return out


def horizon_directions(block):
    return {h: block[str(h)]["direction"] for h in HORIZONS}


def incomplete_horizons(block):
    return [h for h in HORIZONS if block[str(h)]["support"]["support_status"] != "complete"]


def outcome_fns(table):
    if table == "reentry":
        return {h: f"fresh_delta_{h}" for h in HORIZONS}
    return {h: f"increment_{h}_original_dollar" for h in HORIZONS}


# ------------------------------------------------------------------------- readers


def surfaces_input(art, path):
    require_keys(art, SURFACES_REQUIRED, SURFACES_NAME)
    for k in ("states", "reentry"):
        if not isinstance(art[k], list):
            raise SystemExit(f"owned_claim_findings: {SURFACES_NAME} field '{k}' must be a list")
    if not art["days"]:
        raise SystemExit(f"owned_claim_findings: {SURFACES_NAME} carries an empty day list")
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "kind": art["kind"],
        "days": len(art["days"]),
        "day_span": [art["days"][0], art["days"][-1]],
        "source_sha256": art["source_sha256"],
        "event_manifest_sha256": art["event_manifest_sha256"],
        "event_rows": art.get("event_rows"),
        "first_repeat_anchors": art.get("first_repeat_anchors"),
        "units": art["units"],
        "sampling": art["sampling"],
    }


def policy_input(art, path, name):
    require_keys(art, POLICY_REQUIRED, name)
    for section, keys in POLICY_SECTIONS.items():
        rows = art[section]
        if not isinstance(rows, list):
            raise SystemExit(f"owned_claim_findings: {name} section '{section}' must be a list")
        for r in rows:
            require_keys(r, keys, f"{name}:{section}")
    if not art["test_days"]:
        raise SystemExit(f"owned_claim_findings: {name} carries an empty test_days list")
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "kind": art["kind"],
        "source_sha256": art["source_sha256"],
        "model_source_sha256": art.get("model_source_sha256"),
        "test_days": len(art["test_days"]),
        "day_span": [art["test_days"][0], art["test_days"][-1]],
        "model_evidence_flag": art.get("model_evidence_flag"),
        "model_metadata_path": art.get("model_metadata_path"),
        "caveats": art["caveats"],
    }


# ------------------------------------------------------------------------- answers


def build_question(contrasts, index):
    records = []
    for table, family, sl, role in contrasts:
        outcomes = outcome_fns(table)
        for n in VIEWS:
            for clock in CLOCKS:
                block = horizon_block(index[table], n, clock, family, sl, outcomes.__getitem__)
                records.append(
                    {
                        "n": n,
                        "clock": clock,
                        "table": table,
                        "family": family,
                        "slice": dict(sl),
                        "role": role,
                        "immediate_sale_reference_per_100": 0.0,
                        "horizons": block,
                        "horizon_directions": horizon_directions(block),
                        "incomplete_horizons": incomplete_horizons(block),
                        "temporal_contrasts_per_100": temporal_contrasts(block),
                        "slope_120_minus_15_per_100": slope_120_minus_15(block),
                    }
                )
    return records


def build_maturity(index):
    records = []
    for table, family, sl, bucket in MATURITY_CELLS:
        outcomes = outcome_fns(table)
        for n in VIEWS:
            for clock in CLOCKS:
                block = horizon_block(index[table], n, clock, family, sl, outcomes.__getitem__)
                records.append(
                    {
                        "bucket": bucket,
                        "n": n,
                        "clock": clock,
                        "table": table,
                        "family": family,
                        "slice": dict(sl),
                        "immediate_sale_reference_per_100": 0.0,
                        "horizons": block,
                        "horizon_directions": horizon_directions(block),
                        "incomplete_horizons": incomplete_horizons(block),
                        "temporal_contrasts_per_100": temporal_contrasts(block),
                        "slope_120_minus_15_per_100": slope_120_minus_15(block),
                    }
                )
    return records


# --------------------------------------------------------------------- policy diff


def policy_metric(rec, field, scale):
    v = _finite(rec.get(field))
    if v is None:
        return None
    return round(scale * v, 6)


def build_policy_comparison(old, new, old_path, new_path):
    old_sum = index_by(
        old["summary"], ("clock", "n", "side", "policy"), f"{POLICY_BASELINE_NAME}:summary"
    )
    new_sum = index_by(
        new["summary"], ("clock", "n", "side", "policy"), f"{POLICY_FULL_NAME}:summary"
    )
    old_fold = index_by(
        old["folds"], ("fold", "clock", "n", "side", "policy"), f"{POLICY_BASELINE_NAME}:folds"
    )
    new_fold = index_by(
        new["folds"], ("fold", "clock", "n", "side", "policy"), f"{POLICY_FULL_NAME}:folds"
    )
    old_pair = index_by(
        old["paired"],
        ("clock", "n", "side", "policy", "reference"),
        f"{POLICY_BASELINE_NAME}:paired",
    )
    new_pair = index_by(
        new["paired"], ("clock", "n", "side", "policy", "reference"), f"{POLICY_FULL_NAME}:paired"
    )
    old_time = index_by(
        old["timing"], ("clock", "n", "side", "policy"), f"{POLICY_BASELINE_NAME}:timing"
    )
    new_time = index_by(
        new["timing"], ("clock", "n", "side", "policy"), f"{POLICY_FULL_NAME}:timing"
    )

    def diff_block(o, n, fields, scale):
        out = {}
        for f in fields:
            ov = policy_metric(o, f, scale) if o is not None else None
            nv = policy_metric(n, f, scale) if n is not None else None
            out[f] = {
                "baseline": ov,
                "full_discovery": nv,
                "delta_full_minus_baseline": (
                    None if ov is None or nv is None else round(nv - ov, 6)
                ),
            }
        return out

    summary = []
    for key in sorted(set(old_sum) | set(new_sum)):
        o = old_sum.get(key)
        n = new_sum.get(key)
        base = o or n
        rec = {
            "policy": base["policy"],
            "clock": base["clock"],
            "n": base["n"],
            "side": base["side"],
            "presence": "both"
            if key in old_sum and key in new_sum
            else ("baseline_only" if key in old_sum else "full_only"),
        }
        rec.update(diff_block(o, n, ("ev", "fees", "gross_pnl", "median", "day_se"), PER_100))
        rec["known_days"] = {
            "baseline": o and int(o["known_days"]),
            "full_discovery": n and int(n["known_days"]),
        }
        summary.append(rec)

    folds = []
    for key in sorted(set(old_fold) | set(new_fold)):
        o = old_fold.get(key)
        n = new_fold.get(key)
        base = o or n
        rec = {
            "fold": base["fold"],
            "policy": base["policy"],
            "clock": base["clock"],
            "n": base["n"],
            "side": base["side"],
            "presence": "both"
            if key in old_fold and key in new_fold
            else ("baseline_only" if key in old_fold else "full_only"),
        }
        rec.update(diff_block(o, n, ("ev",), PER_100))
        folds.append(rec)

    paired = []
    for key in sorted(set(old_pair) | set(new_pair)):
        o = old_pair.get(key)
        n = new_pair.get(key)
        base = o or n
        rec = {
            "policy": base["policy"],
            "reference": base["reference"],
            "clock": base["clock"],
            "n": base["n"],
            "side": base["side"],
            "presence": "both"
            if key in old_pair and key in new_pair
            else ("baseline_only" if key in old_pair else "full_only"),
        }
        rec.update(diff_block(o, n, ("delta_stop",), PER_100))
        rec["paired_days"] = {
            "baseline": o and int(o["paired_days"]),
            "full_discovery": n and int(n["paired_days"]),
        }
        paired.append(rec)

    timing = []
    for key in sorted(set(old_time) | set(new_time)):
        o = old_time.get(key)
        n = new_time.get(key)
        base = o or n
        rec = {
            "policy": base["policy"],
            "clock": base["clock"],
            "n": base["n"],
            "side": base["side"],
            "presence": "both"
            if key in old_time and key in new_time
            else ("baseline_only" if key in old_time else "full_only"),
        }
        rec.update(diff_block(o, n, ("median_exit_et", "sales_before_13_share"), 1))
        timing.append(rec)

    return {
        "method_rationale": METHOD_RATIONALE,
        "label_methods": LABEL_METHODS,
        "paired_versus_unpaired": (
            "Paired policy deltas (this section) use joint known days only and are distinct from "
            "the unpaired joint-state surface cells, which have no date matching; never read one "
            "as the other."
        ),
        "artifacts": {
            "baseline": policy_input(old, old_path, POLICY_BASELINE_NAME),
            "full_discovery": policy_input(new, new_path, POLICY_FULL_NAME),
        },
        "summary": summary,
        "folds": folds,
        "paired": paired,
        "timing": timing,
    }


# ----------------------------------------------------------------------- provenance

# Lineage keys a producer may DECLARE about its own run. A version string, source
# commit, hash, "corrected" boolean, evidence flag or kind label is a declaration
# only: none of them establishes a corrected source, valid causality or a validated
# edge, so the reader echoes whatever is declared and derives no verification from
# its presence. Absent declarations stay absent, and no observed hash is mapped to a
# commit or source the artifacts do not themselves publish.
DECLARED_LINEAGE_KEYS = (
    "kind",
    "source_sha256",
    "model_source_sha256",
    "model_metadata_path",
    "model_evidence_flag",
    "algorithm",
    "method",
    "run_id",
    "run_provenance",
    "source_commit",
    "source_revision",
    "version",
    "corrected",
)

# Algorithm each policy family declares in owned_claim_contract.json
# (training_contract: the --algorithm value_iteration control versus the
# policy_return controlled refinement). Echoed as a declaration, never as proof of
# causality or of a validated edge.
DECLARED_POLICY_ALGORITHM = {
    "policy_baseline": "fitted_value_iteration_one_step_control",
    "policy_full_discovery": "controlled_refinement_wait_then_complete_policy",
}

# This reader's outputs are always unvalidated financial evidence, independent of
# any source declaration; no declaration can change it.
STRATEGY_VALIDATION = "UNVALIDATED_DISCOVERY_PROXY"


def declared_run_lineage(art):
    return {k: art[k] for k in DECLARED_LINEAGE_KEYS if k in art}


def declared_validation_record(sources):
    """Find an explicit, self-describing source-correction validation record.

    Only a mapping published under a validation/verification-named key counts. A
    bare source_commit, version, hash or corrected flag is NEVER such a record.
    """
    for name, art in sources.items():
        for key, value in art.items():
            low = key.lower()
            if isinstance(value, dict) and ("validat" in low or "verif" in low):
                return {"input": name, "key": key, "record": value}
    return None


def record_declares_verified(record):
    for key in ("status", "result", "verdict", "state"):
        value = record.get(key)
        if isinstance(value, str) and value.strip().lower() in (
            "verified",
            "validated",
            "pass",
            "passed",
            "confirmed",
        ):
            return True
    return record.get("verified") is True or record.get("validated") is True


def build_provenance(inputs, surfaces, policy_old, policy_new):
    sources = {
        "surfaces": surfaces,
        "policy_baseline": policy_old,
        "policy_full_discovery": policy_new,
    }
    lineage = {name: declared_run_lineage(art) for name, art in sources.items()}
    validation = declared_validation_record(sources)
    verified = validation is not None and record_declares_verified(validation["record"])
    source_correction_status = "verified" if verified else "not_verified"
    if verified:
        basis = "explicit declared validation record names a verified source correction"
    elif validation is not None:
        basis = "declared validation record present but does not declare verification"
    else:
        basis = (
            "no explicit validation record in the retained inputs; a generic "
            "version/commit/hash/kind declaration is not proof of correction"
        )
    return {
        "declared_baseline_source_commit": BASELINE_SOURCE_COMMIT,
        "declared_baseline_source_commit_meaning": (
            "Retained-baseline lineage declared by owned_claim_contract.json "
            "(training_contract.controlled_refinement: baseline source/results preserved "
            "at commit76db4a1). It is a declaration carried from the contract, NOT a "
            "hash-to-commit mapping for these inputs: no published registry maps any "
            "observed hash to 76db4a1, and no observed hash is guessed to be it."
        ),
        "declared_run_lineage": lineage,
        "declared_policy_algorithm": DECLARED_POLICY_ALGORITHM,
        "source_correction_status": source_correction_status,
        "source_correction_basis": basis,
        "declared_validation_record": validation,
        "strategy_validation": STRATEGY_VALIDATION,
        "strategy_validation_meaning": (
            "Independent of source lineage: these descriptive and out-of-fold policy "
            "numbers are unvalidated financial evidence, never a validated executable "
            "edge. No source declaration can change this."
        ),
        "observed_hashes": {
            "surfaces": {
                "path": inputs["surfaces"]["path"],
                "artifact_sha256": inputs["surfaces"]["sha256"],
                "source_sha256": inputs["surfaces"]["source_sha256"],
                "event_manifest_sha256": inputs["surfaces"]["event_manifest_sha256"],
            },
            "policy_baseline": {
                "path": inputs["policy_baseline"]["path"],
                "artifact_sha256": inputs["policy_baseline"]["sha256"],
                "source_sha256": inputs["policy_baseline"]["source_sha256"],
                "model_source_sha256": inputs["policy_baseline"]["model_source_sha256"],
            },
            "policy_full_discovery": {
                "path": inputs["policy_full_discovery"]["path"],
                "artifact_sha256": inputs["policy_full_discovery"]["sha256"],
                "source_sha256": inputs["policy_full_discovery"]["source_sha256"],
                "model_source_sha256": inputs["policy_full_discovery"]["model_source_sha256"],
            },
        },
        "provisional": source_correction_status != "verified",
        "note": (
            "Lineage is reported by declaration only. Version, commit, hash, kind and "
            "evidence declarations do not establish a corrected source, valid causality "
            "or a validated edge, so source_correction_status stays not_verified unless an "
            "explicit validation record declares otherwise, and strategy_validation stays "
            "UNVALIDATED_DISCOVERY_PROXY always. Declared pins and the per-family algorithm "
            "are echoed exactly as published; observed hashes are carried unchanged and "
            "never mapped to a commit the artifacts do not publish. New or corrected inputs "
            "are described through their own declared pins instead of being asserted to be "
            "the old baseline, and no run is labelled corrected or verified by guesswork."
        ),
    }


# ------------------------------------------------------------------------------ main


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="checkout root containing factory/artifacts (default: repository root)",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"output path (default: <root>/factory/artifacts/{FINDINGS_NAME})",
    )
    a = ap.parse_args()

    artifacts = a.root / "factory" / "artifacts"
    surfaces_path = artifacts / SURFACES_NAME
    policy_old_path = artifacts / POLICY_BASELINE_NAME
    policy_new_path = artifacts / POLICY_FULL_NAME
    out_path = a.out if a.out is not None else artifacts / FINDINGS_NAME

    surfaces = load_json(surfaces_path, SURFACES_NAME)
    policy_old = load_json(policy_old_path, POLICY_BASELINE_NAME)
    policy_new = load_json(policy_new_path, POLICY_FULL_NAME)

    inputs = {
        "surfaces": surfaces_input(surfaces, surfaces_path),
        "policy_baseline": policy_input(policy_old, policy_old_path, POLICY_BASELINE_NAME),
        "policy_full_discovery": policy_input(policy_new, policy_new_path, POLICY_FULL_NAME),
    }

    index = {
        "surfaces": index_rows(surfaces["states"], f"{SURFACES_NAME}:states"),
        "reentry": index_rows(surfaces["reentry"], f"{SURFACES_NAME}:reentry"),
    }

    provenance = build_provenance(inputs, surfaces, policy_old, policy_new)

    artifact = {
        "kind": "OWNED-CLAIM-LIFECYCLE-DOLLAR-FINDINGS-DESCRIPTIVE-NOT-DEPLOYABLE",
        "provisional": provenance["provisional"],
        "strategy_validation": provenance["strategy_validation"],
        "source_correction_status": provenance["source_correction_status"],
        "horizons": list(HORIZONS),
        "views": list(VIEWS),
        "clocks": list(CLOCKS),
        "horizon_semantics": HORIZON_MEANING,
        "question_semantics": QUESTION_SEMANTICS,
        "units": {
            "surface_increment": (
                "USD per $100 of ORIGINAL claim cash budget (entry sunk, common modeled exit fee)"
            ),
            "policy_return": "portfolio percent of the ORIGINAL equal-dollar book",
            "cash_ruler_per_100": 0.0,
        },
        "mean_basis": {
            "surfaces": (
                "day-balanced (mean of per-day means); claim/event-balanced means not published"
            ),
            "policy": "day mean of portfolio returns; per-claim-balanced means not published",
            "significance": (
                "none computed; day_se and independent known_days reported as published"
            ),
        },
        "calendar": {
            "discovery_days": inputs["surfaces"]["days"],
            "discovery_span": inputs["surfaces"]["day_span"],
            "test_days": inputs["policy_full_discovery"]["test_days"],
            "test_span": inputs["policy_full_discovery"]["day_span"],
            "note": (
                "Exact enumerated day counts, not a contiguous-calendar, 531-day or imputed-day "
                "claim. Known-outcome support varies by cell and horizon and may be fewer than "
                "the date counts; per-cell known_days/known_events/unknown_events are reported "
                "as published."
            ),
        },
        "provenance": provenance,
        "inputs": inputs,
        "questions": {q: build_question(DEF, index) for q, DEF in DEFINED_CONTRASTS.items()},
        "push_vs_damaged_recovery_maturity": build_maturity(index),
        "policy_comparison": build_policy_comparison(
            policy_old, policy_new, policy_old_path, policy_new_path
        ),
        "caveats": FIXED_CAVEATS + list(surfaces["caveats"]),
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(artifact, indent=2, allow_nan=False, sort_keys=False) + "\n")
    print(
        f"Owned-claim findings written {out_path} "
        f"questions={len(artifact['questions'])} "
        f"maturity={len(artifact['push_vs_damaged_recovery_maturity'])} "
        f"policy_summary={len(artifact['policy_comparison']['summary'])}"
    )


if __name__ == "__main__":
    main()
