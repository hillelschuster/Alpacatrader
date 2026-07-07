"""
Phase 2 — Attention scoring engine per SPEC sections 6, 8.

Core flow::

    1. Take an enriched Candidate.
    2. Compute price_attention, volume_attention, hod_acceleration.
    3. Redistribute weight when factors are unavailable (SPEC §6.2).
    4. Apply capped bonuses.
    5. Return ``AttentionScore`` (0–100).

Also provides:

    * Theme detection (SPEC §6.3)
    * Former-runner store (SPEC §6.4) — stub in Phase 2
    * Float-rotation calculation (SPEC §6.5)

Soft annotations moved to ``src/annotations.py`` (T7.2).

No network calls.  No hard filters.  No orders.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from src.models.schemas import AttentionScore, Candidate


# ──────────────────────────────────────────────────────────────────
#  Constants (SPEC-tunable defaults)
# ──────────────────────────────────────────────────────────────────

# Attention factor weights
_PRICE_WEIGHT = 30
_VOLUME_WEIGHT = 40
_HOD_WEIGHT = 30
_TOTAL_WEIGHT = _PRICE_WEIGHT + _VOLUME_WEIGHT + _HOD_WEIGHT  # 100

# ponytail: Top-gainer bot — % gain is the primary signal.
# Cap at 100% so a 250% gainer scores full 30 pts, not capped at 25%.
# A 50% gainer gets 15 pts, a 100%+ gainer gets full 30 pts.
_PRICE_NORMALIZATION_CAP_PCT = 100.0

# Bonuses (capped so total never exceeds 100)
_THEME_BONUS = 10
_FORMER_RUNNER_BONUS = 5
_REPEATED_SCANNER_BONUS = 5
_SCANNER_FRESH_BONUS = 5

# Volume attention sub-weights
_RVOL_SUB_WEIGHT = 25
_DOLLAR_VOL_SUB_WEIGHT = 15

# HOD acceleration sub-weights
_HOD_PROXIMITY_SUB_WEIGHT = 20
_ROC_SUB_WEIGHT = 10

# Minimum available weight to allow entry-scoring
_MIN_AVAILABLE_WEIGHT = 50

# Default "min dollar volume" for normalisation
_DEFAULT_MIN_DOLLAR_VOLUME_5M = 100_000  # $100k

# Former-runner recency window (configurable later)
_FORMER_RUNNER_WINDOW_DAYS = 30

# Float rotation thresholds
_ROTATION_BUILDING = 0.30
_ROTATION_ACTIVE = 1.00
_ROTATION_EXHAUSTION = 2.00


# ──────────────────────────────────────────────────────────────────
#  Core attention scoring
# ──────────────────────────────────────────────────────────────────


def score_attention(
    candidate: Candidate,
    *,
    rvol: Optional[float] = None,
    dollar_volume_5m: Optional[float] = None,
    min_dollar_volume: float = _DEFAULT_MIN_DOLLAR_VOLUME_5M,
    hod_price: Optional[float] = None,
    roc_1m_pct: Optional[float] = None,
    roc_3m_pct: Optional[float] = None,
    roc_5m_pct: Optional[float] = None,
    new_hod_recent: bool = False,
    theme_active: bool = False,
    former_runner: bool = False,
    repeated_scanner_seen: bool = False,
    scanner_seen_count: Optional[int] = None,
) -> AttentionScore:
    """Score a single enriched candidate on attention (0–100).

    Parameters
    ----------
    candidate : Candidate
    rvol : float or None
        Relative volume (RVOL).  *None* if unavailable.
    dollar_volume_5m : float or None
        Trailing 5-minute dollar volume.
    min_dollar_volume : float
        Reference minimum dollar volume for normalisation.
    hod_price : float or None
        Current HOD price.  *None* if unavailable.
    roc_1m_pct, roc_3m_pct, roc_5m_pct : float or None
        Rate-of-change percentages for 1m/3m/5m windows.
    new_hod_recent : bool
        Whether recent bars set the current HOD.
    theme_active : bool
        Whether a relevant theme is active (see ``detect_themes``).
    former_runner : bool
        Whether the symbol is a former runner.
    repeated_scanner_seen : bool
        Legacy flat scanner bonus flag, kept for compatibility.
    scanner_seen_count : int or None
        Consecutive scan-cycle count for this symbol.  A fresh scanner hit gets
        a small bonus that decays away over repeated cycles.

    Returns
    -------
    AttentionScore
    """
    raw_components: dict[str, float] = {}
    drivers: list[str] = []

    # ── Price attention (30 pts) ──────────────────────────────────
    price_pts, price_available = _price_attention(
        candidate.percent_gain, candidate.premarket_gap_pct
    )
    raw_components["price_attention"] = price_pts
    if price_pts > 0:
        drivers.append("top_gainer")

    # ── Volume attention (40 pts) ─────────────────────────────────
    vol_pts, vol_available = _volume_attention(
        rvol=rvol,
        dollar_volume_5m=dollar_volume_5m,
        candidate_volume=candidate.current_volume,
        candidate_price=candidate.price,
        min_dollar_volume=min_dollar_volume,
    )
    raw_components["volume_attention"] = vol_pts
    if vol_pts > 0:
        drivers.append("strong_volume")

    # ── HOD acceleration (30 pts) ─────────────────────────────────
    hod_pts, hod_available = _hod_acceleration(
        price=candidate.price,
        hod_price=hod_price,
        roc_1m_pct=roc_1m_pct,
        roc_3m_pct=roc_3m_pct,
        roc_5m_pct=roc_5m_pct,
        new_hod_recent=new_hod_recent,
    )
    raw_components["hod_acceleration"] = hod_pts
    if hod_pts > 0:
        drivers.append("hod_proximity")

    # ── Redistribution when factors are missing ───────────────────
    available_weight = 0
    raw_score = 0.0
    for label, is_available, weight, pts in [
        ("price_attention", price_available, _PRICE_WEIGHT, price_pts),
        ("volume_attention", vol_available, _VOLUME_WEIGHT, vol_pts),
        ("hod_acceleration", hod_available, _HOD_WEIGHT, hod_pts),
    ]:
        if is_available:
            available_weight += weight
            raw_score += pts

    if available_weight > 0:
        base_score = raw_score * (_TOTAL_WEIGHT / available_weight)
    else:
        base_score = 0.0

    # ── Bonuses (capped so total ≤ 100) ───────────────────────────
    bonuses: float = 0.0
    bonuses_applied: list[str] = []

    if theme_active:
        bonuses += _THEME_BONUS
        bonuses_applied.append("theme_active")
        drivers.append("theme_participant")
    if former_runner:
        bonuses += _FORMER_RUNNER_BONUS
        bonuses_applied.append("former_runner")
        drivers.append("former_runner")
    scanner_fresh_bonus = _scanner_freshness_bonus(scanner_seen_count)
    if scanner_fresh_bonus > 0:
        bonuses += scanner_fresh_bonus
        bonuses_applied.append("scanner_fresh")
    elif scanner_seen_count is None and repeated_scanner_seen:
        bonuses += _REPEATED_SCANNER_BONUS
        bonuses_applied.append("repeated_scanner_seen")

    score = min(100.0, base_score + bonuses)
    score = max(0.0, round(score, 1))

    return AttentionScore(
        score=score,
        drivers=drivers,
        raw_components=raw_components,
        bonuses_applied=bonuses_applied,
    )


# ── Sub-scorers ───────────────────────────────────────────────────


def _price_attention(
    percent_gain: Optional[float],
    premarket_gap_pct: Optional[float],
) -> tuple[float, bool]:
    """Compute price-attention points.

    Uses the larger of ``percent_gain`` and ``premarket_gap_pct``,
    normalised to the roadmap #4 top-gainer cap (25%).

    Returns
    -------
    (points, is_available)
    """
    if percent_gain is not None and premarket_gap_pct is not None:
        best_gain = max(percent_gain, premarket_gap_pct, 0.0)
    elif percent_gain is not None:
        best_gain = max(percent_gain, 0.0)
    elif premarket_gap_pct is not None:
        best_gain = max(premarket_gap_pct, 0.0)
    else:
        return 0.0, False  # unavailable

    pts = min(_PRICE_WEIGHT, best_gain / _PRICE_NORMALIZATION_CAP_PCT * _PRICE_WEIGHT)
    return round(max(0.0, pts), 2), True


def _volume_attention(
    *,
    rvol: Optional[float],
    dollar_volume_5m: Optional[float],
    candidate_volume: Optional[int],
    candidate_price: Optional[float],
    min_dollar_volume: float,
) -> tuple[float, bool]:
    """Compute volume-attention points.

    RVOL contributes up to 25 pts using Phase 2 bands.
    Dollar volume contributes up to 15 pts.

    Returns
    -------
    (points, is_available) — is_available is True if *any* volume data exists.
    """
    pts: float = 0.0
    has_any: bool = False

    # RVOL component
    if rvol is not None:
        has_any = True
        pts += _rvol_points(rvol)

    # Dollar-volume component
    dv: Optional[float] = dollar_volume_5m
    if dv is None and candidate_volume is not None and candidate_price is not None:
        # Rough estimate: assume 5-minute volume ≈ session_volume / 78 (390 min day)
        dv = candidate_volume * candidate_price / 78.0

    if dv is not None and dv > 0:
        has_any = True
        pts += min(_DOLLAR_VOL_SUB_WEIGHT, dv / max(min_dollar_volume, 1.0) * _DOLLAR_VOL_SUB_WEIGHT)

    return round(max(0.0, pts), 2), has_any


def _hod_acceleration(
    *,
    price: Optional[float],
    hod_price: Optional[float],
    roc_1m_pct: Optional[float],
    roc_3m_pct: Optional[float],
    roc_5m_pct: Optional[float],
    new_hod_recent: bool = False,
) -> tuple[float, bool]:
    """Compute HOD-acceleration points.

    HOD proximity: 20 pts within 1% or recent new HOD,
    ~10.6 pts within 3%, else 0.
    ROC: best of 1m/3m/5m / 5% * 10 pts, capped at 10.

    Returns
    -------
    (points, is_available) — is_available is True if HOD data OR ROC data exists.
    """
    pts: float = 0.0
    has_any: bool = False

    # HOD proximity / recent new HOD
    proximity_pts = 0.0
    if price is not None and hod_price is not None and hod_price > 0:
        has_any = True
        dist_pct = (hod_price - price) / hod_price * 100.0
        if dist_pct <= 1.0:
            proximity_pts = _HOD_PROXIMITY_SUB_WEIGHT
        elif dist_pct <= 3.0:
            proximity_pts = _HOD_PROXIMITY_SUB_WEIGHT * 0.53  # ~10.6 pts
    if new_hod_recent:
        has_any = True
        proximity_pts = max(proximity_pts, float(_HOD_PROXIMITY_SUB_WEIGHT))
    pts += proximity_pts

    # ROC
    roc_vals = [v for v in (roc_1m_pct, roc_3m_pct, roc_5m_pct) if v is not None]
    if roc_vals:
        has_any = True
        best_roc = max(max(roc_vals), 0.0)
        pts += min(_ROC_SUB_WEIGHT, best_roc / 5.0 * _ROC_SUB_WEIGHT)

    return round(max(0.0, pts), 2), has_any


def _rvol_points(rvol: float) -> float:
    """RVOL bands per SPEC §11.17.5: <2 weak, 2-3 moderate,
    3-5 strong, >=5 capped.
    """
    rvol = max(0.0, rvol)
    if rvol < 2.0:
        return rvol / 2.0 * 10.0
    if rvol < 3.0:
        return 10.0 + (rvol - 2.0) * 5.0
    if rvol < 5.0:
        return 15.0 + (rvol - 3.0) / 2.0 * 10.0
    return float(_RVOL_SUB_WEIGHT)


def _scanner_freshness_bonus(scanner_seen_count: Optional[int]) -> float:
    """Small scanner-hit bonus that decays across repeated scan cycles."""
    if scanner_seen_count is None or scanner_seen_count <= 0:
        return 0.0
    if scanner_seen_count == 1:
        return float(_SCANNER_FRESH_BONUS)
    if scanner_seen_count == 2:
        return 3.0
    if scanner_seen_count == 3:
        return 1.0
    return 0.0


# ──────────────────────────────────────────────────────────────────
#  Theme detection (SPEC §6.3)
# ──────────────────────────────────────────────────────────────────


def detect_themes(
    candidates: list[Candidate],
    *,
    top_n: int = 10,
    min_shared: int = 3,
) -> dict[str, list[str]]:
    """Find active themes among top-ranked candidates.

    A theme is active when at least ``min_shared`` of the top ``top_n``
    candidates share a country, sector, or industry.

    Parameters
    ----------
    candidates : list[Candidate]
        Attention-ranked candidates (highest first).
    top_n : int
        How many top candidates to consider.
    min_shared : int
        Minimum number of candidates sharing a value for it to be a theme.

    Returns
    -------
    dict[str, list[str]]
        Keys are theme labels (e.g. ``"country:China"``, ``"sector:Healthcare"``).
        Values are the symbols participating in that theme.
    """
    top = candidates[:top_n]
    themes: dict[str, list[str]] = {}

    for label, field in [("country", "country"), ("sector", "sector"), ("industry", "industry")]:
        values = [(c.symbol, getattr(c, field, None)) for c in top]
        grouped: dict[str, list[str]] = {}
        for sym, val in values:
            if val:
                grouped.setdefault(val, []).append(sym)

        for val, syms in grouped.items():
            if len(syms) >= min_shared:
                themes[f"{label}:{val}"] = syms

    return themes


def is_symbol_in_theme(
    candidate: Candidate, themes: dict[str, list[str]]
) -> bool:
    """Return ``True`` if the candidate participates in any active theme."""
    for theme_key, syms in themes.items():
        if candidate.symbol in syms:
            return True
    return False


# ──────────────────────────────────────────────────────────────────
#  Former-runner store (SPEC §6.4) — Phase 2 stub
# ──────────────────────────────────────────────────────────────────


class FormerRunnerStore:
    """In-memory store of symbols that previously exhibited runner behavior.

    Phase 2 stub — on first run the store is empty, which is expected
    per SPEC §6.4.  Later phases persist this across sessions.
    """

    def __init__(self) -> None:
        self._runners: dict[str, datetime] = {}  # symbol → last_seen

    def mark(self, symbol: str, when: Optional[datetime] = None) -> None:
        """Record that ``symbol`` was a notable runner."""
        self._runners[symbol] = when or datetime.now(timezone.utc)

    def is_runner(self, symbol: str, *, within_days: int = _FORMER_RUNNER_WINDOW_DAYS) -> bool:
        """Return ``True`` if ``symbol`` is a known former runner within the window."""
        when = self._runners.get(symbol)
        if when is None:
            return False
        age_days = (datetime.now(timezone.utc) - when).days
        return age_days <= within_days

    def __len__(self) -> int:
        return len(self._runners)

    def __contains__(self, symbol: str) -> bool:
        return self.is_runner(symbol)


# ──────────────────────────────────────────────────────────────────
#  Float rotation (SPEC §6.5)
# ──────────────────────────────────────────────────────────────────


def calculate_float_rotation(
    candidate: Candidate,
    session_cumulative_volume: Optional[int] = None,
) -> Optional[float]:
    """Compute float rotation ratio.

    rotation = cumulative_session_volume / float_shares

    Returns *None* if float is unknown or <= 0.
    """
    if candidate.float_shares is None or candidate.float_shares <= 0:
        return None
    if session_cumulative_volume is None:
        session_cumulative_volume = candidate.current_volume or 0
    if session_cumulative_volume <= 0:
        return 0.0
    return round(session_cumulative_volume / candidate.float_shares, 4)


def float_rotation_label(rotation: Optional[float]) -> Optional[str]:
    """Human-readable label for a float-rotation ratio.

    Returns one of: ``"building"``, ``"active"``, ``"exhaustion"``, or *None*.
    """
    if rotation is None:
        return None
    if rotation < _ROTATION_BUILDING:
        return "building"
    if rotation < _ROTATION_ACTIVE:
        return "active"
    if rotation < _ROTATION_EXHAUSTION:
        return "watch_for_exhaustion"
    return "exhaustion"


# ──────────────────────────────────────────────────────────────────
#  Convenience: score a batch
# ──────────────────────────────────────────────────────────────────


def score_candidates(
    candidates: list[Candidate],
    *,
    min_dollar_volume: float = _DEFAULT_MIN_DOLLAR_VOLUME_5M,
    former_runner_store: Optional[FormerRunnerStore] = None,
    scanner_seen_counts: Optional[dict[str, int]] = None,
) -> list[tuple[Candidate, AttentionScore]]:
    """Score a list of candidates and return them sorted by attention (descending).

    In Phase 2, only scanner-level data is available (price, percent_gain,
    volume, sector, country, etc.).  Quote/bar data is not yet enriched.
    Factors that require missing data are redistributed per SPEC §6.2.

    Parameters
    ----------
    candidates : list[Candidate]
    min_dollar_volume : float
        Reference min dollar volume for normalisation.
    former_runner_store : FormerRunnerStore or None
    scanner_seen_counts : dict[str, int] or None
        Consecutive scan-cycle counts used for the decaying scanner-fresh bonus.

    Returns
    -------
    list[tuple[Candidate, AttentionScore]]
        Sorted by attention score descending.
    """
    themes = detect_themes(candidates)

    scored: list[tuple[Candidate, AttentionScore]] = []
    for c in candidates:
        is_runner = (
            former_runner_store.is_runner(c.symbol)
            if former_runner_store is not None
            else False
        )
        score = score_attention(
            c,
            rvol=c.relative_volume,
            dollar_volume_5m=c.dollar_volume,
            min_dollar_volume=min_dollar_volume,
            hod_price=c.day_high,
            theme_active=is_symbol_in_theme(c, themes),
            former_runner=is_runner,
            scanner_seen_count=(scanner_seen_counts or {}).get(c.symbol),
            # Batch rank has no bars yet → ROC unavailable.
        )
        scored.append((c, score))

    scored.sort(key=lambda x: x[1].score, reverse=True)
    return scored
