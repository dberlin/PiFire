from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AugerTiming:
    pulse_s: int = 2
    frame_s: int = 20

    def __post_init__(self) -> None:
        if (
            isinstance(self.pulse_s, bool)
            or isinstance(self.frame_s, bool)
            or not isinstance(self.pulse_s, int)
            or not isinstance(self.frame_s, int)
            or self.pulse_s <= 0
            or self.frame_s <= 0
        ):
            raise ValueError("auger pulse and frame durations must be positive integers")
        if self.frame_s % self.pulse_s:
            raise ValueError("auger frame duration must be divisible by pulse duration")


AUGER_TIMING = AugerTiming()
