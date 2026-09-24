from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, assert_never

try:
    from factory.scripts import basket_sim as sim
except ImportError:
    import basket_sim as sim

BLOCKS = {"block1": ("2021-02", "2023-12"), "block2": ("2025-02", "2026-05")}
PATTERNS = {"25_25_rest": (0.25, 0.25), "33_33_rest": (0.33, 0.33), "50_rest": (0.50,)}


class F4ContractError(RuntimeError):
    pass


class F4ConfigurationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Trigger:
    kind: Literal["R2", "R3"]
    L: int | None = None
    w: int | None = None
    g: int | None = None

    @property
    def trigger_id(self) -> str:
        match self.kind:
            case "R2":
                return f"R2_L{self.L}_w{self.w}"
            case "R3":
                return f"R3_g{self.g}"
            case unreachable:
                assert_never(unreachable)

    def rule(self):
        match self.kind:
            case "R2":
                return sim.R2(-int(self.L), int(self.w))
            case "R3":
                return sim.R3(int(self.g))
            case unreachable:
                assert_never(unreachable)


@dataclass(frozen=True, slots=True)
class Cell:
    kind: Literal["hold", "full_exit", "partial"]
    bps: int
    trigger_id: str | None = None
    pattern_id: str | None = None

    @property
    def run_id(self) -> str:
        match self.kind:
            case "hold":
                return f"A_pm_N2_hold_bps{self.bps}"
            case "full_exit":
                return f"A_pm_N2_{self.trigger_id}_full_exit_bps{self.bps}"
            case "partial":
                return f"A_pm_N2_{self.trigger_id}_{self.pattern_id}_bps{self.bps}"
            case unreachable:
                assert_never(unreachable)


def build_triggers() -> list[Trigger]:
    return ([Trigger("R2", L=L, w=w) for L in (10, 15) for w in (3, 5, 10)] +
            [Trigger("R3", g=g) for g in (40, 50, 60)])


def build_cells() -> list[Cell]:
    triggers = build_triggers()
    cells = [Cell("hold", bps=bps) for bps in (100, 150)]
    cells += [Cell("full_exit", bps, trigger.trigger_id)
              for trigger in triggers for bps in (100, 150)]
    cells += [Cell("partial", bps, trigger.trigger_id, pattern)
              for trigger in triggers for pattern in PATTERNS for bps in (100, 150)]
    return cells


def trigger_from_id(trigger_id: str) -> Trigger:
    try:
        return next(trigger for trigger in build_triggers() if trigger.trigger_id == trigger_id)
    except StopIteration as error:
        raise F4ConfigurationError(f"unregistered F4 trigger: {trigger_id}") from error


class ScaleOutRule:
    """Stage partials on subsequent trigger firings; final trigger releases the rest."""

    def __init__(self, trigger: Trigger, fractions: tuple[float, ...]):
        if not fractions or any(fraction not in (0.25, 0.33, 0.50) for fraction in fractions):
            raise F4ConfigurationError("only registered current-share REDUCE fractions are allowed")
        self.trigger = trigger.rule()
        self.fractions = fractions
        self.name = f"F4({trigger.trigger_id},{','.join(f'{f:g}' for f in fractions)})"
        self._tickets: dict[tuple[str, str], sim.Ticket] = {}

    def state(self, tk: sim.Ticket) -> dict:  # noqa: DICT_OK — simulator owns mutable per-rule state
        return tk.rule_state.setdefault(self.name, {})

    def evaluate(self, tk: sim.Ticket, bar: dict, idx: int):
        self._tickets[(tk.sleeve_day, tk.ticker)] = tk
        state = self.state(tk)
        stage = state.setdefault("stage", 0)
        if state.get("awaiting_execution"):
            if tk.n_reduces <= state["reduce_count_at_signal"]:
                return None
            state["awaiting_execution"] = False
            state["stage"] += 1
            stage += 1
        action = self.trigger.evaluate(tk, bar, idx)
        if action is None:
            return None
        if stage >= len(self.fractions):
            state["stage"] = 0
            state["awaiting_execution"] = False
            return {"action": "EXIT", "level": action.get("level"), "reason": self.name}
        state["awaiting_execution"] = True
        state["reduce_count_at_signal"] = tk.n_reduces
        return {"action": "REDUCE", "frac": self.fractions[stage],
                "level": action.get("level"), "reason": f"{self.name}:stage{stage + 1}"}


class FullExitRule:
    def __init__(self, trigger: Trigger):
        self.trigger = trigger.rule()
        self.name = f"F4_FULL({trigger.trigger_id})"
        self._tickets: dict[tuple[str, str], sim.Ticket] = {}

    def evaluate(self, tk: sim.Ticket, bar: dict, idx: int):
        self._tickets[(tk.sleeve_day, tk.ticker)] = tk
        action = self.trigger.evaluate(tk, bar, idx)
        if action is None:
            return None
        return {"action": "EXIT", "level": action.get("level"), "reason": self.name}


def strategy(cell: Cell) -> sim.StrategySpec:
    match cell.kind:
        case "full_exit":
            release = [FullExitRule(trigger_from_id(cell.trigger_id))]
        case "partial":
            release = [ScaleOutRule(trigger_from_id(cell.trigger_id), PATTERNS[cell.pattern_id])]
        case "hold":
            release = [sim.R0()]
        case unreachable:
            assert_never(unreachable)
    return sim.StrategySpec(family_id="F4", entry_pop="A_pm", entry_T=570,
                            top_n=2, n_slots=2, reserve_frac=1.0,
                            release=release, name=cell.run_id)


def hold_spec() -> sim.StrategySpec:
    return sim.StrategySpec(family_id="F4", entry_pop="A_pm", entry_T=570,
                            top_n=2, n_slots=2, release=[sim.R0()], name="hold")


def validate_day(day: str) -> None:
    sim.guard_day(day)
    if not (("2021-02-01" <= day <= "2023-12-31") or
            ("2025-02-01" <= day <= "2026-05-31")):
        raise F4ConfigurationError(f"day outside frozen development blocks: {day}")


def block_for_day(day: str) -> str:
    for name, (lo, hi) in BLOCKS.items():
        if lo <= day[:7] <= hi:
            return name
    raise F4ConfigurationError(f"day outside development blocks: {day}")
