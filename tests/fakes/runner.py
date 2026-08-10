from collections import deque
from dataclasses import replace
from typing import Any

from common.control_trace import ActuationMode, ControllerType
from controller.model_learning.contracts import FrameObservation
from controller.runtime.runner import (
    ObservationOutcomeDrain,
    ObservationOutcomeEnvelope,
    ObservationSubmission,
    ObservationTerminalDrop,
)


class FakeControllerRunner:
    def __init__(
        self,
        period=None,
        commands_fan=False,
        wants_async=False,
        actuation_mode=ActuationMode.FRAMED_PULSE,
        controller_type: ControllerType | None = None,
    ):
        self._script = []
        self._i = 0
        self.target = None
        self._period = period
        self.submitted_temps = []
        self.calibration_requests = []
        self.calibration_cancellations = []
        self._commands_fan = commands_fan
        self._wants_async = wants_async
        self._actuation_mode = actuation_mode
        self._controller_type = controller_type
        self._configuration_revision = 0
        self.applied = []
        self.restored = []
        self.activation_restores = []
        self.activation_failures = []
        self.activation_rollbacks = []
        self.activation_events = []
        self.snapshot: dict[str, Any] | None = None
        self.observations = []
        self._observation_sequence = 0
        self._observation_outcomes = []
        self._terminal_drops_since_drain = deque()
        self.observation_outcome = None
        # A single ordered log across restore_model()/set_output() calls, since
        # `restored` and `applied` are separate lists and so cannot express
        # relative ordering between a restore and the report that follows it.
        self._outcome_drops_since_drain = 0
        self._outcome_dropped_sequences = deque(maxlen=60)
        self._evidence_contexts = {}
        self.calls = []
        self.refits = 0
        self.refit_raises = None
        self.refit_verdict: object | None = None
        self.stops = 0
        # How many stop() calls had happened at each refit_from_cook() call, so
        # a test can hold the refit to after the worker was asked to stop
        # without reading the two counters as if they were ordered.
        self.stops_before_each_refit = []
        self.safety_ceiling_c = None

    def script(self, outputs):
        self._script = list(outputs)
        self._i = 0
        return self

    def set_target(self, setpoint):
        self.target = setpoint

    def set_safety_ceiling_c(self, ceiling_c):
        self.safety_ceiling_c = ceiling_c

    def request_calibration(self, command):
        self.calibration_requests.append(command)

    def cancel_calibration(self, reason):
        self.calibration_cancellations.append(reason)

    def restore_activation(self, persisted, records):
        self.activation_restores.append((persisted, tuple(records)))
        return True

    def activation_runtime_failure(self, reason):
        self.activation_failures.append(reason)
        return True

    def rollback_activation(self, reason):
        self.activation_rollbacks.append(reason)
        return True

    def drain_activation_events(self):
        events = tuple(self.activation_events)
        self.activation_events.clear()
        return events

    def submit(self, temp):
        self.submitted_temps.append(temp)

    def reconfigure(self, settings, control, logger=None):
        self._configuration_revision += 1
        return "Active"

    def control_period(self):
        return self._period

    def commands_fan(self):
        return self._commands_fan

    def wants_async(self):
        return self._wants_async

    def actuation_mode(self):
        return self._actuation_mode

    def controller_type(self):
        return self._controller_type

    def configuration_revision(self):
        return self._configuration_revision

    def runs_async(self):
        return self._wants_async

    def bind_evidence_context(self, generation, session_id, cook_id):
        self._evidence_contexts[generation] = (session_id, cook_id)

    def retire_evidence_context(self, generation):
        self._evidence_contexts.pop(generation, None)

    def stop(self):
        self.stops += 1

    def set_output(self, applied):
        self.applied.append(applied)
        self.calls.append(("apply", applied))

    def complete_frame(self, applied, observation: FrameObservation):
        self.set_output(applied)
        return self.observe_frame(observation)

    def observe_frame(self, observation: FrameObservation):
        self._observation_sequence += 1
        owned = replace(observation)
        self.observations.append(owned)
        if self.observation_outcome is not None:
            if len(self._observation_outcomes) == 30:
                dropped = self._observation_outcomes.pop(0)
                self._outcome_drops_since_drain += 1
                self._outcome_dropped_sequences.append(dropped.submission_sequence)
                self._terminal_drops_since_drain.append(
                    ObservationTerminalDrop(
                        dropped.submission_sequence,
                        dropped.configuration_generation,
                        dropped.observation,
                        "runner-outcome-evicted",
                    )
                )
            self._observation_outcomes.append(
                ObservationOutcomeEnvelope(
                    self._observation_sequence, self._configuration_revision, owned, self.observation_outcome
                )
            )
        return ObservationSubmission(self._observation_sequence, self._configuration_revision)

    def drain_observation_outcomes(self):
        envelopes = []
        withheld = []
        for envelope in self._observation_outcomes:
            if envelope.configuration_generation in self._evidence_contexts:
                envelopes.append(envelope)
            else:
                withheld.append(envelope)
        self._observation_outcomes = withheld
        terminal_drops = []
        withheld_drops = deque()
        for drop in self._terminal_drops_since_drain:
            if drop.configuration_generation in self._evidence_contexts:
                terminal_drops.append(drop)
            else:
                withheld_drops.append(drop)
        self._terminal_drops_since_drain = withheld_drops
        drain = ObservationOutcomeDrain(
            tuple(envelopes),
            tuple(terminal_drops),
            self._outcome_drops_since_drain,
            tuple(self._outcome_dropped_sequences),
        )
        self._outcome_drops_since_drain = 0
        self._outcome_dropped_sequences.clear()
        return drain

    def get_model_snapshot(self):
        return self.snapshot

    def restore_model(self, snapshot):
        self.restored.append(snapshot)
        self.calls.append(("restore", snapshot))
        return snapshot is not None

    def refit_from_cook(self):
        self.refits += 1
        self.stops_before_each_refit.append(self.stops)
        if self.refit_raises:
            raise self.refit_raises
        return self.refit_verdict

    def controller_state(self):
        return {"fake": True}

    def latest(self):
        if not self._script:
            return None
        out = self._script[min(self._i, len(self._script) - 1)]
        self._i += 1
        return out
