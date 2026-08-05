"""Fixed deterministic scenario definitions for the linear-MPC bake-off."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ScenarioDefinition:
    """A one-second plant schedule evaluated by a twenty-second controller."""

    name: str
    target_low_c: float
    target_high_c: float
    step_at_s: int | None = None
    lid_start_s: int | None = None
    lid_duration_s: int = 0
    safety_override_start_s: int | None = None
    safety_override_duration_s: int = 0
    manual_override_start_s: int | None = None
    manual_override_duration_s: int = 0
    applicable_plants: tuple[str, ...] = ("GrillSim", "MAKGrillSim")

    def target_at(self, second: int) -> float:
        if self.step_at_s is not None and second >= self.step_at_s:
            return self.target_high_c
        return self.target_low_c

    def lid_open_at(self, second: int) -> bool:
        return self.lid_start_s is not None and self.lid_start_s <= second < self.lid_start_s + self.lid_duration_s

    def safety_override_at(self, second: int) -> bool:
        return (
            self.safety_override_start_s is not None
            and self.safety_override_start_s <= second
            < self.safety_override_start_s + self.safety_override_duration_s
        )

    def manual_override_at(self, second: int) -> bool:
        return (
            self.manual_override_start_s is not None
            and self.manual_override_start_s <= second
            < self.manual_override_start_s + self.manual_override_duration_s
        )


# The complete matrix deliberately includes low/middle/high, both step directions,
# a hold, and a physical lid disturbance. Quick mode truncates duration, never rows.
SCENARIOS = (
    ScenarioDefinition("low-step", 55.0, 75.0, 60),
    ScenarioDefinition("middle-step", 90.0, 120.0, 60),
    ScenarioDefinition("high-step-450f", 145.0, (450.0 - 32.0) * 5.0 / 9.0, 60),
    ScenarioDefinition(
        "high-step-600f", 175.0, (600.0 - 32.0) * 5.0 / 9.0, 60, applicable_plants=("GrillSim",)
    ),
    ScenarioDefinition("down-step", 120.0, 85.0, 60),
    ScenarioDefinition("long-hold", 110.0, 110.0),
    ScenarioDefinition("lid-excursion", 110.0, 110.0, lid_start_s=80, lid_duration_s=30),
    ScenarioDefinition(
        "override-window",
        110.0,
        110.0,
        safety_override_start_s=80,
        safety_override_duration_s=20,
        manual_override_start_s=100,
        manual_override_duration_s=20,
    ),
)


def quick_scenarios() -> tuple[ScenarioDefinition, ...]:
    """Return the minimal deterministic smoke subset without changing its semantics."""
    return tuple(
        item for item in SCENARIOS if item.name in {"low-step", "lid-excursion", "override-window"}
    )
