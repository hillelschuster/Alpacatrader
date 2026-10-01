# CV01 experiment A — current-dollar continuation curve (ContinuationAnatomy)

code=2089a7c36a86 contract=3c599dd36569

Experiment A only. Experiment B (giveback-10 retain vs sell-close-reclaim-reenter) is owned by OwnershipContinuity; this producer neither computes nor publishes B.

Pins declared before any CV number was inspected: clock bucket=30 ET-min (anchor ET570), tenure bucket=15 bars, state coordinate=dist_from_running_high with edges [-inf,-0.20,-0.10,-0.05,-0.02,0.02,+inf], quantile subsample=every 16th eligible decision state.

A: CV=P_future/P_exit-1 (common 50bp fee cancels), immediate endpoint CV=0, horizon from both decision_et and immediate exit et; gaps missing (real ET elapsed, never imputed); censored/partial paths kept and counted, never zeroed. All 1,888,885 clock-eligible opportunities are retained in the risk set; the 185 with no next print stay UNRESOLVED (no CV, counted, never dropped/zeroed). Means/contributions/min/max exact; quantiles subsampled+labelled per cell by *_n.

Views: mean_occ = occupancy-weighted (each occupied member-minute pair equally). mean_member = equal-member day/family path: mean the supported outcomes WITHIN a member, average members WITHIN the (day,family) unit, then average units; units with no support stay unknown (never 0). Uncertainty is day- and month-clustered over (day,family) units on the equal-member mean only; minute snapshots are NOT independent units.

Time/tenure confound (explicit): the clock and tenure baselines are mechanically confounded (a later decision clock implies a longer tenure for the same session), so both are descriptive decompositions of the same curve, not independent causal effects. Family/block/month splits are descriptive strata, not causal controls.

Anatomy only: no optimal value, no alpha/EV claim, no policy, no sizing, no sub-minute data.

Marginal single-coordinate rulers (bounds declared before any CV was computed; diagnostic bins, not a threshold policy; missing = explicit UNKNOWN bucket, never 0): ret_from_fill edges [-inf,0,0.10,0.30,1,+inf]; volume_accel edges [-inf,-0.5,0,1,+inf]; ret_percentile_candidates native 0..1 (verified) quartile bands [0,.25,.5,.75,1]. No joint grid, no model, no Atlas/NN features.
