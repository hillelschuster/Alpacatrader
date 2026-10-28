# Entry microstructure assignment — status

## Verdict
Not decision-ready. The new producer is an exploratory per-baseline-attempt quote sensitivity, not the requested complete book-level counterfactual. It has not been run, so it contains no measured variant table and supports no hard-coded bot recommendation. Do not treat this as completed acceptance.

## Evidence
Existing audited depth-lane baseline (from `adaptive_depth_sizing/results.json`): chosen `thr300bps|p50`; validation +1.36 $/panel session at 25 bps, 53 known fills, 8 unknown executions; late +5.95 $/panel session, 122 fills and 20 unknown executions. Existing contract describes entry UNKNOWN mechanisms: stale-but-valid arrival, partial arrival depth, invalid/non-regular/absent quote, or missing acquisition; late cause breakdown and conversion behavior remain unmeasured here.

## What was created
`factory/scripts/alpha_entry_microstructure.py` reads the existing frozen thr300bps|p50 funded trades and shared quote reader and sketches exact-ask, looser limits, pullback, and spread-gate sensitivities. Its declared scope explicitly does not replay slot/cash/cooldown re-admission, so fills and P&L cannot be compared as strategy-level performance. It also does not yet implement/report the full required age gate, exits at bid versus bid-minus-tick, detailed cost ladder summaries, or variant aggregate statistics. It must not be used to select an entry/exit policy.

## Required to close
Replace/complete the producer so each variant is replayed through depth lane admission, book constraints and causal execution clocks; include quote age >1s, all four spread caps, all pullback TTL/price combinations and both exit prices; report per-block fills, unknown share/reasons, median known trade P&L, top-five share and session/calendar-day contribution at 0/25/50/100 bps; select only on validation and show late alongside it. Then run it on the permitted validation and late blocks. Keep protected dates unread. No best bot microstructure can responsibly be named until those results exist.