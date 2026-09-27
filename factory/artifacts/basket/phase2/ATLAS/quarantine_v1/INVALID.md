# quarantine_v1 — the v1 ATLAS panel is INVALID. Do not use.

The v1 `panel.parquet` (sha256 `8ce4615946e2f5eed41e32858bb4020246a448720e81d74cebf8db2d1b7d990c`,
1,900,432 rows, 6,160 members) was **withdrawn on 2026-09-25** after an independent foundation
review. It is deliberately **not preserved** here (273 MB); only these small v1 sidecars are kept as
the record of what was produced and why it was wrong.

Defects that invalidate v1 (all fixed in v2):

1. **Uncensored terminal values.** 273 members have no print at the session close, yet their
   `v_hold_flat`, no-trigger `v_giveback_*` and the four session constants were valued at the last
   available close — an unobservable, non-executable terminal liquidation, concentrated in exactly
   the halt-like paths the thesis cares about.
2. **Same-bar execution.** A give-back trigger on the member's final bar was "executed" at that same
   bar's close, violating the contract's next-open execution rule.
3. **Wrong terminal price.** The no-trigger give-back fallback used a close; the engine schedules
   `FORCED_FLAT` on the completed bar `session_end-1` and executes it at the **open** of the
   `session_end` bar. v1 therefore overstated/mis-stated every no-trigger continuation.
4. **Future metadata as a key.** `member_last_et` (the member's last print ET) was registered as a
   `key` although it is only knowable after the session.
5. **Incomplete acceptance assertions.** The C1 cross-check asserted only member-set equality and
   cell counts — not `entry_px`, `entry_et`, `entry_rank` or the engine's exit prices — and treated a
   missing C1 reference as a passing check.
6. **Unreproducible verification.** The broad differential claim had no checked-in verifier and no
   boundary strata.

Contents: `SCHEMA_v1.md`, `README_v1_asof_quarantine.md`, `coverage_v1.json`, `selftest_v1.json`,
`_progress_v1.json`, `differential_check_v1.json`, `smoke_v1/` (v1 smoke self-test and verification).
The live v2 artifacts are the ones one directory up.
