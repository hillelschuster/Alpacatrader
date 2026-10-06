#!/usr/bin/env python3
"""Frozen H025 forward IEX shadow; data.alpaca.markets GET only, never trades.

Every poll is a durable transaction in a per-session journal. Only current,
completed, fresh IEX bars can create decisions; session history warms features,
not hypothetical past orders. Execution scenarios are IEX bar models, NOT SIP
execution, queue probabilities, or development-feed parity. See forward README.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

ET = ZoneInfo('America/New_York')
UTC = timezone.utc
API = 'https://data.alpaca.markets'
QUALIFICATION = dict(rank_max=3, gain_min=1.0, pullback_min=-.01,
                     r15_min=.03, r15_period=15)


@dataclass(frozen=True)
class Policy:
    rank_max: int = 3
    gain_min: float = 1.0
    pullback_min: float = -.01
    r15_min: float = .03
    r15_period: int = 15
    prior_flush_min: int = 0
    discount: float = .10
    stop: float = .10
    target: float = 1.0
    hold_bars: int = 30
    hold_minutes: int | None = None
    expiry_minutes: int = 120
    refresh: bool = True
    rearm: bool = True
    friction_bps: float = 100.0
    order_notional: float | None = None
    flush_counter: str = 'legacy_zero'
    fresh_state_only: bool = False


def stamp(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('timestamps must include timezone')
    return dt.astimezone(UTC)


def iso(dt):
    return dt.astimezone(UTC).isoformat()


def frozen(path):
    raw = path.read_bytes()
    doc = json.loads(raw)
    if doc.get('schema_version') != 1:
        raise ValueError('schema_version must be 1')
    stamp(doc['frozen_at'])
    policies = {}
    for name in ('baseline', 'candidate'):
        p = Policy(**doc[name])
        for k in ('gain_min', 'pullback_min', 'r15_min'):
            if not isinstance(getattr(p, k), (int, float)) or not math.isfinite(getattr(p, k)):
                raise ValueError(f'invalid qualifier {k}')
        for k in ('rank_max', 'r15_period', 'prior_flush_min', 'hold_bars', 'expiry_minutes'):
            n = getattr(p, k)
            if type(n) is not int or n < (0 if k in ('prior_flush_min', 'expiry_minutes') else 1):
                raise ValueError(f'invalid policy {k}')
        if p.flush_counter not in ('legacy_zero', 'carried') or type(p.fresh_state_only) is not bool:
            raise ValueError('invalid flush_counter/fresh_state_only')
        if p.order_notional is not None and (not isinstance(p.order_notional, (int, float))
                                            or not math.isfinite(p.order_notional) or p.order_notional <= 0):
            raise ValueError('order_notional must be positive or null')
        if not (0 <= p.discount < 1 and 0 <= p.stop < 1 and p.target > 0
                and math.isfinite(p.target) and math.isfinite(p.friction_bps)
                and p.friction_bps >= 0):
            raise ValueError('invalid policy prices/friction')
        if p.hold_minutes is not None and (type(p.hold_minutes) is not int or p.hold_minutes < 0):
            raise ValueError('hold_minutes must be positive integer or null')
        if type(p.refresh) is not bool or type(p.rearm) is not bool:
            raise ValueError('refresh/rearm must be boolean')
        policies[name] = asdict(p)
    sessions = doc.get('sessions', {})
    if not isinstance(sessions, dict):
        raise ValueError('sessions must be a calendar mapping')
    for day, sess in sessions.items():
        op, cl = session_times(day, sess)
        if op >= cl or sess['previous_session'] >= day:
            raise ValueError('invalid session/calendar reference')
    symbols = doc.get('symbols')
    if symbols is not None and (not isinstance(symbols, list) or not symbols
                               or any(not isinstance(s, str) or not s.isalnum() for s in symbols)):
        raise ValueError('symbols must be a nonempty symbol list')
    age = doc.get('max_source_age_seconds', 180)
    if not isinstance(age, (int, float)) or not math.isfinite(age) or age <= 0:
        raise ValueError('invalid max_source_age_seconds')
    return doc, policies, hashlib.sha256(raw).hexdigest()


def session_times(day, sess):
    return tuple(datetime.fromisoformat(f'{day}T{sess[k]}').replace(tzinfo=ET)
                 .astimezone(UTC) for k in ('open', 'close'))


class MarketData:
    """No configurable host/path: credentials can only reach read-only data GETs."""
    def __init__(self):
        self.headers = {'APCA-API-KEY-ID': os.environ['ALPACA_API_KEY'],
                        'APCA-API-SECRET-KEY': os.environ['ALPACA_SECRET_KEY']}

    def get(self, path, **params):
        assert path in ('/v1beta1/screener/stocks/movers', '/v2/stocks/snapshots', '/v2/stocks/bars')
        url = API + path + '?' + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=self.headers, method='GET')
        with urllib.request.urlopen(req, timeout=25) as response:
            return json.load(response)

    def collect(self, doc, tracked, start, end):
        if doc.get('symbols'):
            rows = [{'symbol': s, 'source': 'frozen_watchlist'} for s in doc['symbols']]
        else:
            movers = self.get('/v1beta1/screener/stocks/movers', top=50)
            rows = [dict(r, source='alpaca_screener') for r in movers.get('gainers', [])]
        symbols = sorted({r['symbol'] for r in rows} | set(tracked))
        if not symbols:
            return rows, {}, {}, iso(datetime.now(UTC))
        snapshots = self.get('/v2/stocks/snapshots', symbols=','.join(symbols), feed='iex')
        bars = {s: [] for s in symbols}
        token = None
        while True:
            params = dict(symbols=','.join(symbols), timeframe='1Min', start=iso(start),
                          end=iso(end), feed='iex', adjustment='raw', limit=10000, sort='asc')
            if token:
                params['page_token'] = token
            response = self.get('/v2/stocks/bars', **params)
            for s, values in (response.get('bars') or {}).items():
                bars.setdefault(s, []).extend(values)
            token = response.get('next_page_token')
            if not token:
                break
        return rows, snapshots, bars, iso(datetime.now(UTC))



def exchange_calendar(client, cache, now):
    """Cache actual exchange holidays/early closes; GET /v2/calendar only."""
    existing = json.loads(cache.read_text()) if cache.exists() else None
    day = now.astimezone(ET).date().isoformat()
    if existing is None or existing['through'] < day:
        start = (now - timedelta(days=14)).date().isoformat()
        end = (now + timedelta(days=400)).date().isoformat()
        url = 'https://paper-api.alpaca.markets/v2/calendar?' + urllib.parse.urlencode(dict(start=start, end=end))
        req = urllib.request.Request(url, headers=client.headers, method='GET')
        with urllib.request.urlopen(req, timeout=25) as response:
            rows = json.load(response)
        if not isinstance(rows, list) or len(rows) < 2:
            raise ValueError('empty/invalid exchange calendar')
        existing = dict(source='GET https://paper-api.alpaca.markets/v2/calendar',
                        received_at=iso(datetime.now(UTC)), through=end, rows=rows)
        temp = cache.with_suffix('.tmp')
        temp.write_text(json.dumps(existing, allow_nan=False))
        temp.replace(cache)
    sessions = {}
    rows = sorted(existing['rows'], key=lambda r: r['date'])
    for previous, current in zip(rows, rows[1:]):
        sessions[current['date']] = dict(open=current['open'], close=current['close'],
                                        previous_session=previous['date'])
    digest = hashlib.sha256(cache.read_bytes()).hexdigest()
    return sessions, dict(source=existing['source'], received_at=existing['received_at'], sha256=digest)

def restore(path, digest):
    """Recover complete committed polls, trimming only our interrupted last write."""
    state = {'symbols': {}, 'last_decision_minute': None}
    if not path.exists():
        return state, 0
    end, seq = 0, 0
    with path.open('rb') as handle:
        for line in handle:
            if not line.endswith(b'\n'):
                break
            rec = json.loads(line)
            if rec['frozen_sha256'] != digest:
                raise ValueError('existing journal has different frozen SHA256; use a new output directory')
            state, seq = rec['state'], rec['sequence']
            end = handle.tell()
    with path.open('r+b') as handle:
        handle.truncate(end)
    return state, seq


def commit(path, rec):
    with path.open('a') as handle:
        handle.write(json.dumps(rec, allow_nan=False, separators=(',', ':')) + '\n')
        handle.flush()
        os.fsync(handle.fileno())


def features(bars, reference, open_at, cutoff, periods=(15,)):
    by_time = {}
    for b in bars:
        t = stamp(b['t'])
        values = [float(b[k]) for k in ('o', 'h', 'l', 'c')]
        if any(not math.isfinite(v) or v <= 0 for v in values):
            raise ValueError('invalid OHLC')
        if not (b['l'] <= min(b['o'], b['c']) <= max(b['o'], b['c']) <= b['h']):
            raise ValueError('inconsistent OHLC')
        if open_at <= t and t + timedelta(minutes=1) <= cutoff:
            by_time[t] = b
    ordered = sorted(by_time)
    if not ordered:
        return None, []
    closes, running, prior, under, last = {}, 0.0, 0, False, None
    clock = open_at
    final = None
    while clock <= ordered[-1]:
        b = by_time.get(clock)
        if b is not None:
            last = float(b['c'])
        if last is not None:
            running = max(running, last)
            closes[clock] = last
            old = closes.get(clock - timedelta(minutes=15))
            # Count NEW low-bar episode starts, excluding the current start.
            before = prior
            if b is not None:
                below = float(b['l']) <= .9 * running
                if below and not under:
                    prior += 1
                under = below
            if b is not None:
                final = dict(c0=last, gain=last / reference - 1,
                             pullback=last / running - 1,
                             r15=last / old - 1 if old else None,
                             returns={str(period): last / closes[clock-timedelta(minutes=period)] - 1
                                      if clock-timedelta(minutes=period) in closes else None
                                      for period in periods},
                             prior_flush=before, bar_start=iso(clock),
                             known_at=iso(clock + timedelta(minutes=1)))
        clock += timedelta(minutes=1)
    return final, [by_time[t] for t in ordered]


def lifecycle(branch, bars, p, now, events, key):
    """Only touch orders activated before a bar starts; never hindsight entries."""
    last = branch.get('last_bar')
    for b in bars:
        t = stamp(b['t'])
        if last is not None and t <= stamp(last):
            continue
        branch['last_bar'] = b['t']
        order = branch.get('order')
        pos = branch.get('position')
        just_filled = False
        if order and t >= stamp(order['expires_at']):
            events.append(dict(event='shadow_expire', branch=key, bar=b['t'], order=order))
            branch.pop('order')
            order = None
        if order and t >= stamp(order['active_at']) and float(b['l']) <= order['buy']:
            pos = dict(buy=order['buy'], target=order['target'], stop=order['buy'] * (1-p['stop']),
                       count=0, fill_bar=b['t'], quantity=order['quantity'])
            branch['position'] = pos
            branch.pop('order')
            branch['ever_filled'] = True
            just_filled = True
            events.append(dict(event='shadow_touch_fill', branch=key, bar=b['t'], position=dict(pos),
                               entry_gap=float(b['o']) < order['buy']))
        if pos:
            pos['count'] += 1
            reason, price = None, None
            if (key.endswith('/optimistic') and float(b['h']) >= pos['target']
                    and not (pos['count'] > 1 and float(b['o']) <= pos['stop'])):
                reason, price = 'target', pos['target']
            elif float(b['l']) <= pos['stop']:
                reason, price = 'stop_first', min(float(b['o']), pos['stop'])
            elif float(b['h']) >= pos['target'] and (not just_filled or float(b['c']) >= pos['target']):
                reason, price = 'target', pos['target']
            elif p['hold_minutes'] is not None and (t - stamp(pos['fill_bar'])).total_seconds() >= 60*p['hold_minutes']:
                reason, price = 'hold_clock_minutes', float(b['c'])
            elif p['hold_minutes'] is None and pos['count'] >= p['hold_bars']:
                reason, price = 'hold_new_bars', float(b['c'])
            if reason:
                events.append(dict(event='shadow_model_exit', branch=key, bar=b['t'], reason=reason,
                                   price=price, stop_gap=(pos['stop']-price)/pos['stop'] if reason=='stop_first' else 0,
                                   net_return=price / pos['buy'] - 1 - p['friction_bps']/10000,
                                   quantity=pos['quantity'],
                                   net_order_dollars=(price-pos['buy']-pos['buy']*p['friction_bps']/10000)*pos['quantity']
                                   if pos['quantity'] is not None else None))
                branch.pop('position')
    # Wall-clock expiry still applies even if IEX printed no bars.
    if branch.get('order') and now >= stamp(branch['order']['expires_at']):
        events.append(dict(event='shadow_expire', branch=key, order=branch.pop('order')))


def poll(doc, policies, state, client, now):
    day = now.astimezone(ET).date().isoformat()
    sess = doc['sessions'].get(day)
    if not sess:
        return dict(status='session_closed', reason='calendar_not_authorized', events=[])
    op, cl = session_times(day, sess)
    if now < op or now >= cl:
        terminal = []
        if now >= cl:
            for symbol, branches in state['symbols'].items():
                for key, branch in branches.items():
                    if branch.get('order'):
                        terminal.append(dict(event='shadow_session_cancel', symbol=symbol, branch=key, order=branch.pop('order')))
                    if branch.get('position') and not branch.get('terminal_unresolved'):
                        branch['terminal_unresolved'] = True
                        terminal.append(dict(event='unresolved_end_of_session_not_scored', symbol=symbol,
                                             branch=key, position=branch['position']))
        return dict(status='session_closed', reason='outside_session', events=terminal)
    if now < stamp(doc['frozen_at']):
        return dict(status='wait', reason='freeze_not_effective', events=[])
    cutoff = now.replace(second=0, microsecond=0)
    rows, snapshots, raw, received = client.collect(doc, state['symbols'], op, cutoff)
    received_at = stamp(received)
    decision_at = received_at.replace(second=0, microsecond=0)
    if received_at >= cl:
        return dict(status='session_closed', reason='collection_crossed_close', events=[], received_at=received,
                    source_rows=rows, snapshots=snapshots, bars=raw)
    candidates, quality, all_bars = {}, {}, {}
    scan_symbols = {r['symbol'] for r in rows}
    for symbol in sorted(set(raw) | scan_symbols | set(state['symbols'])):
        try:
            snapshot = snapshots.get(symbol, {})
            prev = next((snapshot.get(k) for k in ('dailyBar', 'prevDailyBar')
                         if snapshot.get(k) and stamp(snapshot[k]['t']).astimezone(ET).date().isoformat() == sess['previous_session']), None)
            # Active intents can observe valid bars even if reference data fails.
            _, all_bars[symbol] = features(raw.get(symbol, []), 1.0, op, cutoff)
            if not prev:
                quality[symbol] = 'missing_or_stale_previous_session_reference'
                continue
            reference = float(prev['c'])
            if not math.isfinite(reference) or reference <= 0:
                raise ValueError('invalid previous close')
            f, bars = features(raw.get(symbol, []), reference, op, cutoff,
                               periods=tuple(p['r15_period'] for p in policies.values()))
            all_bars[symbol] = bars
            if not f:
                quality[symbol] = 'missing_completed_rth_bars'
                continue
            age = (received_at - stamp(f['known_at'])).total_seconds()
            if age > doc.get('max_source_age_seconds', 180):
                quality[symbol] = 'stale_iex_bar'
                continue
            if stamp(f['known_at']) != decision_at:
                quality[symbol] = 'wait_for_current_completed_minute'
                continue
            f.update(reference=reference, reference_time=prev['t'], age_seconds=age)
            candidates[symbol] = f
            quality[symbol] = 'current_completed_iex_raw_reference_checked'
        except (ValueError, TypeError, KeyError) as exc:
            quality[symbol] = 'error:' + str(exc)
    # Retain the complete observed screener ordering, including names lacking
    # IEX bars; dropping those names before ranking would spuriously promote others.
    scanner_order = [r['symbol'] for r in rows]
    ranked = [s for s in scanner_order if s in candidates]
    for symbol in ranked:
        candidates[symbol]['rank'] = scanner_order.index(symbol) + 1
        candidates[symbol]['rank_source'] = 'observed_screener_order_or_frozen_watchlist_order'
    events = []
    # Replay only observations for already-active virtual intents, not past decisions.
    for symbol, branches in state['symbols'].items():
        for key, branch in branches.items():
            first = len(events)
            lifecycle(branch, all_bars.get(symbol, []), policies[key.split('/')[0]], received_at, events, key)
            for event in events[first:]:
                event['symbol'] = symbol
    fresh_decision = state['last_decision_minute'] != iso(decision_at)
    opportunities = []
    for symbol in ranked:
        f = candidates[symbol]
        opportunities.append(dict(symbol=symbol, **f))
        if not fresh_decision:
            continue
        branches = state['symbols'].setdefault(symbol, {})
        active = decision_at + timedelta(minutes=1)  # first entire bar after poll receipt
        for name, p in policies.items():
            r = f['returns'][str(p['r15_period'])]
            if not (f['rank'] <= p['rank_max'] and f['gain'] >= p['gain_min']
                    and f['pullback'] >= p['pullback_min'] and r is not None and r >= p['r15_min']):
                continue
            for ordering in ('pessimistic', 'optimistic'):
                key = name + '/' + ordering
                branch = branches.setdefault(key, {'last_bar': f['bar_start']})
                if branch.get('position') or (branch.get('ever_filled') and not p['rearm']):
                    continue
                if f['prior_flush'] < p['prior_flush_min']:
                    continue
                if branch.get('order') and not p['refresh']:
                    continue
                buy = f['c0']*(1-p['discount'])
                quantity = int(p['order_notional']/buy) if p['order_notional'] is not None else None
                if quantity == 0:
                    events.append(dict(event='shadow_quantity_block', symbol=symbol, branch=key, buy=buy))
                    continue
                order = dict(buy=buy, quantity=quantity, target=f['c0']*p['target'],
                             state_known_at=f['known_at'], active_at=iso(active),
                             expires_at=iso(stamp(f['known_at'])+timedelta(minutes=p['expiry_minutes'])))
                event = 'shadow_refresh' if branch.get('order') else 'shadow_intent'
                branch['order'] = order
                events.append(dict(event=event, symbol=symbol, branch=key, order=order,
                                   opportunity_known_at=f['known_at']))
    if candidates:
        state['last_decision_minute'] = iso(decision_at)
    status = 'observing' if ranked else ('missing' if not rows or not any(raw.values()) else 'wait')
    if not ranked and any(q.startswith('error:') for q in quality.values()):
        status = 'error'
    return dict(status=status, reason='current_input' if ranked else 'no_usable_current_input',
                received_at=received, source_rows=rows, snapshots=snapshots, bars=raw,
                quality=quality, features=candidates, opportunities=opportunities, events=events,
                universe='current_screener_50_or_frozen_watchlist_not_development_universe',
                ordering_note='scenario_frontier_not_monotonic_portfolio_bounds')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--frozen', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--env-file', type=Path)
    ap.add_argument('--once', action='store_true')
    ap.add_argument('--probe-inputs', action='store_true',
                    help='Read-only IEX snapshots even outside session; never creates decisions')
    ap.add_argument('--poll-seconds', type=float, default=15)
    args = ap.parse_args()
    if args.poll_seconds <= 0 or not math.isfinite(args.poll_seconds):
        ap.error('--poll-seconds must be positive')
    if args.env_file:
        from dotenv import load_dotenv
        load_dotenv(args.env_file, override=False)
    doc, policies, digest = frozen(args.frozen)
    frozen_parameters = json.loads(args.frozen.read_bytes())
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / '.shadow.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        client, session_day, path, state, seq = None, None, None, None, 0
        while True:
            now = datetime.now(UTC)
            day = now.astimezone(ET).date().isoformat()
            if day != session_day:
                folder = args.output / day
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / 'journal.jsonl'
                state, seq = restore(path, digest)
                session_day = day
            calendar_provenance = {'source': 'unavailable'}
            try:
                if hashlib.sha256(args.frozen.read_bytes()).hexdigest() != digest:
                    raise ValueError('frozen file changed while running; restart with a new output directory')
                calendar_provenance = {'source': 'explicit_frozen_sessions'}
                if not frozen_parameters.get('sessions'):
                    if client is None:
                        client = MarketData()
                    doc['sessions'], calendar_provenance = exchange_calendar(client, args.output / 'calendar.json', now)
                sess = doc['sessions'].get(day)
                if sess and session_times(day, sess)[0] <= now < session_times(day, sess)[1] and client is None:
                    client = MarketData()
                if args.probe_inputs:
                    if client is None:
                        client = MarketData()
                    symbols = doc.get('symbols') or ['SPY', 'AAPL']
                    snapshots = client.get('/v2/stocks/snapshots', symbols=','.join(symbols), feed='iex')
                    received = datetime.now(UTC)
                    diagnostic = {}
                    for symbol, snapshot in snapshots.items():
                        bar = snapshot.get('minuteBar')
                        previous = next((snapshot.get(k) for k in ('dailyBar', 'prevDailyBar')
                                         if snapshot.get(k) and sess and
                                         stamp(snapshot[k]['t']).astimezone(ET).date().isoformat() == sess['previous_session']), None)
                        age = (received - stamp(bar['t']) - timedelta(minutes=1)).total_seconds() if bar else None
                        diagnostic[symbol] = dict(bar_age_seconds=age,
                            previous_reference_time=previous.get('t') if previous else None,
                            fresh=age is not None and 0 <= age <= doc.get('max_source_age_seconds', 180))
                    result = dict(status='wait', reason='diagnostic_only_no_decisions', events=[],
                                  received_at=iso(received), snapshots=snapshots, quality=diagnostic)
                else:
                    result = poll(doc, policies, state, client, now)
            except Exception as exc:
                result = dict(status='error', reason=type(exc).__name__ + ': ' + str(exc), events=[])
            seq += 1
            rec = dict(sequence=seq, poll_started_at=iso(now), journaled_at=iso(datetime.now(UTC)),
                       frozen_sha256=digest, frozen_parameters=frozen_parameters, effective_policies=policies,
                       calendar_provenance=calendar_provenance,
                       producer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                       source_feed='iex', source_adjustment='raw', non_trading=True,
                       development_feed_parity=False, **result, state=state)
            commit(path, rec)
            print(json.dumps({k: rec[k] for k in ('sequence', 'status', 'reason', 'frozen_sha256')},
                             allow_nan=False), flush=True)
            if args.once:
                return 1 if result['status'] == 'error' else 0
            time.sleep(args.poll_seconds)


if __name__ == '__main__':
    raise SystemExit(main())
