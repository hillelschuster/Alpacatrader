#!/usr/bin/env python3
"""Discovery-only selection audit, not an outcome or corrected-board backtest.

Demonstrates why post-ranking filtering does not repair wrong denominators or
split-contaminated rank. Keeps all frozen market data untouched.
"""
import argparse
import hashlib
import json
from pathlib import Path

import polars as pl


def board_path(root: Path, day: str) -> Path:
    if not ('2021-02-01' <= day <= '2023-03-14'):
        raise ValueError('only discovery dates may be inspected')
    return root / 'atlas/observation/v0/race.minute_full' / f'month={day[:7]}' / f'{day}.parquet'


def audit(root: Path) -> dict:
    amv_path = board_path(root, '2022-09-28')
    amv = (pl.scan_parquet(amv_path)
           .filter((pl.col('ticker') == 'AMV') & (pl.col('t') == 679))
           .select(['t', 'ticker', 'px', 'px_et', 'gain', 'rank_known',
                    'prev_close', 'prev_close_et', 'prev_close_day',
                    'prev_close_source', 'prevclose_vs_sip_clast_ratio',
                    'flag_prevclose_discrepancy']).collect())
    retained_amv = (pl.scan_parquet(root / 'entry_ev/stage_a/2022-09-28.parquet')
                    .filter((pl.col('ticker') == 'AMV') & (pl.col('t') == 679))
                    .select(['t', 'ticker', 'rank_known', 'prev_close']).collect())
    net_prior = (pl.scan_parquet(root / 'sip/net/bars/2022-09-27.parquet')
                 .filter(pl.col('ticker') == 'AMV').select(['et', 'close']).collect())

    # Corporate-action audit uses only the action effective on this discovery day.
    split_path = root / 'harvest01/base/splits.parquet'
    actions = (pl.scan_parquet(split_path).filter(pl.col('ex_date') == '2021-03-01')
               .select(['symbol', 'ex_date', 'old_rate', 'new_rate']).collect())
    factors_frame = actions.group_by('symbol').agg(
        (pl.col('old_rate') / pl.col('new_rate')).product().alias('factor'))
    factors = dict(factors_frame.iter_rows())
    snap = (pl.scan_parquet(board_path(root, '2021-03-01'))
            .filter((pl.col('t') == 585) & pl.col('rank_eligible'))
            .select(['ticker', 'px', 'prev_close', 'gain', 'rank_known']).collect())
    old = snap.sort(['rank_known', 'ticker']).head(10)
    # This is deliberately NOT a full repaired board: only split arithmetic changes.
    adjusted = snap.with_columns(
        pl.col('ticker').replace_strict(factors, default=1.0, return_dtype=pl.Float64)
        .alias('split_multiplier')).with_columns(
            (pl.col('px') / (pl.col('prev_close') * pl.col('split_multiplier')) - 1)
            .alias('gain_counterfactual')).sort(
                ['gain_counterfactual', 'ticker'], descending=[True, False])
    new = adjusted.head(10)
    retained = (pl.scan_parquet(root / 'entry_ev/stage_a/2021-03-01.parquet')
                .filter(pl.col('t') == 585).select(['ticker', 'rank_known']).collect())
    gs = adjusted.filter(pl.col('ticker') == 'GHSI').select(
        ['ticker', 'px', 'prev_close', 'gain', 'split_multiplier', 'gain_counterfactual'])

    return {
        'schema': 'entry_ev/selection_integrity_audit/v1',
        'producer_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'scope': 'discovery inputs only; selection/provenance, no future-return evaluation',
        'frozen_board_modified': False,
        'population_repaired': False,
        'amv': {
            'original_state': amv.to_dicts(),
            'retained_by_stage_a': retained_amv.to_dicts(),
            'independent_prior_netbar_rows': net_prior.height,
            'within_session_close_gap_minutes': 959 - amv['prev_close_et'][0],
            'reading': 'The old guard retained a non-null above-$1 denominator from an '
                       'earlier prior-session print; the independent comparison is null '
                       'and its discrepancy flag is false. Missing verification is not a pass.',
            'official_close_status': 'Prior audit reported 82.12 at a 16:00 bar stamp. '
                                     'Under the bar-start contract that stamp is outside '
                                     'RTH. Official-close identity requires an authoritative '
                                     'closing-price/auction source; not certified here.'
        },
        'ghsi': {
            'corporate_actions': actions.to_dicts(),
            'original_and_split_adjusted': gs.to_dicts(),
            'original_top10': old.to_dicts(),
            'split_adjusted_top10': new.select(
                ['ticker', 'gain_counterfactual', 'rank_known']).to_dicts(),
            'old_top10_after_posthoc_filter': retained.to_dicts(),
            'replacement_names': sorted(set(new['ticker'].to_list()) - set(old['ticker'].to_list())),
            'reading': 'Split adjustment occurs before sorting the FULL cross-section. '
                       'Dropping the contaminated leader after top10 selection loses '
                       'the original rank11 replacement. This counterfactual verifies '
                       'selection mechanics; other denominators remain unchanged/unvalidated.'
        },
        'required_repair': [
            'Evidence-backed previous ACTUAL session closing price per ticker, not merely '
            'the last available intraday bar or a non-null value.',
            'Explicit verified/unverified/discrepant/missing denominator states; null '
            'independent comparison is not verified agreement.',
            'Corporate-action normalization between the denominator session and decision '
            'day under the intended scanner contract.',
            'Apply universe/quality filters and validated gains before full-universe '
            'reranking; rebuild dependent selections in a separate versioned data root.',
            'Do not claim the frozen H042 or ENTRY-EV populations were repaired.'
        ]
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-root', type=Path, default=Path('/home/hillel/projects/Alpacatrader/data'))
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    result = audit(args.data_root)
    args.out.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({
        'amv_retained_rows': len(result['amv']['retained_by_stage_a']),
        'amv_prior_print_gap_minutes': result['amv']['within_session_close_gap_minutes'],
        'ghsi_adjustment': result['ghsi']['original_and_split_adjusted'],
        'replacement_names': result['ghsi']['replacement_names'],
        'population_repaired': False
    }))


if __name__ == '__main__':
    main()
