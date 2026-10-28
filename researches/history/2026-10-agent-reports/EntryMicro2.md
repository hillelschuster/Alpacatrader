# Entry / exit microstructure replay

## Harness proof and scope

- Replayed the full funded `thr300bps|p50` book with `alpha_adaptive_depth_sizing.replay_day`, importing the lane quote readers and pricing legs. Variant hooks monkeypatch intent eligibility and pricing only; book/capital/cooldown logic is unchanged. Per-day variant files are resumable.
- Scope: validation 2023 (250 sessions; 361 calendar days) and late confirmation 2025-02..2026-05 (332 sessions; 481 calendar days). The 2024, 2025-01, and 2026-06..08 blocks were excluded.
- Baseline reproduced: validation $1.363527/session at 25 bps (target $1.3635; delta +$0.000027), late $5.949121/session (target $5.9491; delta +$0.000021). Baseline late count: 122 known / 20 UNKNOWN.
- $ is known net contribution only (UNKNOWN excluded). Session = panel session; calendar day = inclusive span day. Costs are residual round-trip bps. UNKNOWN share = unknown / funded attempts. Median is per-fill net at 25 bps. Top-5 share = sum of five largest known 25-bps contributions / total known contribution; it can exceed 100% because losses reduce the denominator.

## Variant results

| Variant | V/L $/session 25 | V/L $/calendar day 25 | V/L $/session 50 | V/L $/calendar day 50 | Known fills V/L | UNKNOWN share V/L | Median fill $25 V/L | Top-5 share V/L |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 1.364/5.949 | 0.944/4.106 | 0.998/5.296 | 0.691/3.656 | 53/122 | 12.9%/14.1% | -5.491/-9.214 | 465.3%/124.6% |
| entry_ask | 1.550/4.755 | 1.073/3.282 | 1.192/4.114 | 0.826/2.839 | 52/116 | 12.5%/13.1% | -6.919/-9.206 | 412.0%/138.0% |
| entry_ask_plus_1tick | 1.352/5.628 | 0.936/3.884 | 0.985/4.975 | 0.682/3.434 | 53/118 | 12.9%/14.0% | -5.491/-9.206 | 470.3%/132.2% |
| entry_ask_x1.005 | 1.349/5.978 | 0.934/4.127 | 0.982/5.324 | 0.680/3.675 | 53/121 | 12.9%/14.1% | -5.491/-9.139 | 470.7%/124.5% |
| entry_ask_x1.01 | 1.360/5.938 | 0.942/4.099 | 0.995/5.284 | 0.689/3.648 | 53/122 | 12.9%/14.1% | -5.491/-9.214 | 466.4%/125.0% |
| spread_le_50bps | -0.908/8.541 | -0.629/5.895 | -1.046/8.209 | -0.724/5.666 | 22/67 | 4.3%/11.8% | -15.811/-3.193 | -229.6%/78.8% |
| spread_le_100bps | -2.265/6.958 | -1.569/4.802 | -2.577/6.341 | -1.785/4.377 | 44/116 | 13.5%/14.1% | -12.536/-9.124 | -143.4%/106.5% |
| spread_le_200bps | 1.467/5.949 | 1.016/4.106 | 1.104/5.296 | 0.765/3.656 | 52/122 | 13.1%/14.1% | -6.919/-9.214 | 432.6%/124.6% |
| spread_le_400bps | 1.364/5.949 | 0.944/4.106 | 0.998/5.296 | 0.691/3.656 | 53/122 | 12.9%/14.1% | -5.491/-9.214 | 465.3%/124.6% |
| age_le_0.5s | 0.837/4.509 | 0.580/3.112 | 0.551/3.952 | 0.381/2.728 | 40/103 | 10.9%/10.4% | -10.014/-9.289 | 683.0%/172.8% |
| age_le_1s | 0.980/6.314 | 0.679/4.358 | 0.654/5.692 | 0.453/3.929 | 47/114 | 7.7%/12.3% | -8.347/-9.214 | 618.2%/117.4% |
| age_le_2s | 1.364/5.949 | 0.944/4.106 | 0.998/5.296 | 0.691/3.656 | 53/122 | 12.9%/14.1% | -5.491/-9.214 | 465.3%/124.6% |
| exit_bid | 1.364/5.949 | 0.944/4.106 | 0.998/5.296 | 0.691/3.656 | 53/122 | 12.9%/14.1% | -5.491/-9.214 | 465.3%/124.6% |
| exit_bid_minus_tick | 1.364/5.949 | 0.944/4.106 | 0.998/5.296 | 0.691/3.656 | 53/122 | 12.9%/14.1% | -5.491/-9.214 | 465.3%/124.6% |
| exit_bid_x0.995 | 1.364/5.949 | 0.944/4.106 | 0.998/5.296 | 0.691/3.656 | 53/122 | 12.9%/14.1% | -5.491/-9.214 | 465.3%/124.6% |
| wait_mid_5s | 2.732/4.486 | 1.892/3.097 | 2.404/3.860 | 1.665/2.664 | 49/117 | 21.0%/17.6% | -8.450/-11.033 | 232.7%/169.3% |

Other required cost rungs, known $/session validation/late (0 bps; 100 bps):
- `baseline`: 0 bps 1.729/6.602; 100 bps 0.267/3.991.
- `entry_ask`: 0 bps 1.908/5.397; 100 bps 0.477/2.831.
- `entry_ask_plus_1tick`: 0 bps 1.719/6.280; 100 bps 0.251/3.669.
- `entry_ask_x1.005`: 0 bps 1.715/6.633; 100 bps 0.249/4.014.
- `entry_ask_x1.01`: 0 bps 1.726/6.592; 100 bps 0.264/3.977.
- `spread_le_50bps`: 0 bps -0.769/8.873; 100 bps -1.322/7.546.
- `spread_le_100bps`: 0 bps -1.954/7.574; 100 bps -3.200/5.108.
- `spread_le_200bps`: 0 bps 1.829/6.602; 100 bps 0.380/3.991.
- `spread_le_400bps`: 0 bps 1.729/6.602; 100 bps 0.267/3.991.
- `age_le_0.5s`: 0 bps 1.124/5.066; 100 bps -0.022/2.837.
- `age_le_1s`: 0 bps 1.306/6.935; 100 bps 0.001/4.450.
- `age_le_2s`: 0 bps 1.729/6.602; 100 bps 0.267/3.991.
- `exit_bid`: 0 bps 1.729/6.602; 100 bps 0.267/3.991.
- `exit_bid_minus_tick`: 0 bps 1.729/6.602; 100 bps 0.267/3.991.
- `exit_bid_x0.995`: 0 bps 1.729/6.602; 100 bps 0.267/3.991.
- `wait_mid_5s`: 0 bps 3.059/5.113; 100 bps 1.748/2.607.

## Validation choice and recommendation

Highest validation 25-bps known $/session: **wait_mid_5s**, $2.7316; late is $4.4864/session ($3.0967/calendar day). This partial-basis winner has late UNKNOWN share 17.6% vs baseline 14.1%. Paper-bot candidate: wait-mid 5s, with explicit caution that this quote-conditional study is not a fill guarantee. Late known return is below baseline ($4.4864 vs $5.9491/session at 25 bps), so don’t claim robust out-of-sample superiority. If retaining the existing lane is preferred, the baseline remains the conservative choice.

## Late baseline UNKNOWN causes and conversions

| Cause | Count |
|---|---:|
| entry stale | 3 |
| entry no-depth | 1 |
| exit stale | 5 |
| exit no-depth | 11 |
| halt/gap | 0 |

| Variant converting baseline UNKNOWNs | Converted fills | Converted-known contribution (25 bps) | Converted-known contribution (50 bps) | Cause split |
|---|---:|---:|---:|---|
| wait_mid_5s | 3 | $313.83 | $306.06 | entry stale: 2, entry no-depth: 1 |

Only wait-mid converted baseline UNKNOWNs: three (two entry stale, one entry no-depth), accounting for $313.83 at 25 bps and $306.06 at 50 bps. Other variants convert none. `halt/gap` is zero in this late baseline; unknown exit-depth and exit-stale are categorized separately.

## Artifacts

- Runner: `factory/scripts/alpha_entry_microstructure.py`.
- Results: `~/alpha-data/open-search-v1/entry_microstructure/results.json`; resumable per-day JSON under `~/alpha-data/open-search-v1/entry_microstructure/days/{validation,confirmation}/`.
- Existing lane sources were imported and not edited; only allowed date blocks were selected.
