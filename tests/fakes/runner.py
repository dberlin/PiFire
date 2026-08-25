from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from common.control_trace import ActuationMode, ControllerType
from common.model_evidence import ModelEvidenceRecord
from common.persistence.model_evidence import ModelActivationState
from controller.model_learning.contracts import FrameObservation
from controller.runtime.model_fitting import TeardownRefitOutcome
from controller.runtime.model_persistence import DurableActivationReceipt
from controller.runtime.observation_buffer import ObservationOutcomeBuffer
from controller.runtime.runner import (
    ObservationOutcomeEnvelope,
    ObservationSubmission,
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
        self.restore_outcome = None
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
        self.activation_confidences = []
        self.finalized_refits = []
        self.finished_teardowns = 0
        self.snapshot: dict[str, Any] | None = None
        self.observations = []
        self.frame_completions = []
        self._observation_sequence = 0
        self._observation_buffer = ObservationOutcomeBuffer(capacity=30)
        self.observation_outcome = None
        # A single ordered log across restore_model()/set_output() calls, since
        # `restored` and `applied` are separate lists and so cannot express
        # relative ordering between a restore and the report that follows it.
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

    def restore_activation(
        self,
        persisted: ModelActivationState,
        records: Sequence[ModelEvidenceRecord],
    ) -> bool:
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

    def submit_activation_confidence(
        self,
        record: ModelEvidenceRecord,
    ) -> DurableActivationReceipt | None:
        self.activation_confidences.append(record)
        receipt = DurableActivationReceipt(accepted=True)
        receipt._complete(durable=True)
        return receipt

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

    def drain_restore_outcome(self):
        outcome = self.restore_outcome
        self.restore_outcome = None
        return outcome

    def runs_async(self):
        return self._wants_async

    def bind_evidence_context(self, generation, session_id, cook_id):
        self._observation_buffer.bind_context(generation, session_id, cook_id)

    def retire_evidence_context(self, generation):
        self._observation_buffer.retire_context(generation)

    def stop(self):
        self.stops += 1

    def stop_for_refit(self) -> bool | None:
        self.stop()
        return None

    def finalize_cook_refit(self, outcome: TeardownRefitOutcome) -> bool:
        self.finalized_refits.append(outcome)
        return True

    def finish_teardown(self) -> None:
        self.finished_teardowns += 1

    def set_output(self, applied):
        self.applied.append(applied)
        self.calls.append(("apply", applied))

    def complete_frame(self, applied, observation: FrameObservation):
        self.frame_completions.append((applied, observation))
        self.set_output(applied)
        return self.observe_frame(observation)

    def observe_frame(self, observation: FrameObservation):
        self._observation_sequence += 1
        owned = replace(observation)
        self.observations.append(owned)
        if self.observation_outcome is not None:
            self._observation_buffer.append_outcome(
                ObservationOutcomeEnvelope(
                    self._observation_sequence,
                    self._configuration_revision,
                    owned,
                    self.observation_outcome,
                )
            )
        return ObservationSubmission(self._observation_sequence, self._configuration_revision)

    def append_observation_outcome(
        self,
        envelope: ObservationOutcomeEnvelope,
    ) -> None:
        self._observation_buffer.append_outcome(envelope)

    def drain_observation_outcomes(self):
        return self._observation_buffer.drain()

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
