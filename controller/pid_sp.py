"""
*****************************************
 PiFire PID Controller with a Smith Predictor
*****************************************

 Description: The auto-centering PID controller, regulating on a temperature
 with identified dead time removed instead of on the raw probe reading.

 This controller models nothing itself. controller/fopdt_identifier.py learns
 the grill's FOPDT parameters passively from applied duty, and
 controller/smith_predictor.py turns those parameters into one temperature per
 tick. That single value drives P, I and D -- the derivative compares
 consecutive SELECTED temperatures and never subtracts a measured sample from a
 predicted one.

 Until the identifier's gates clear, the selected temperature is the measured
 one, and outside the three-cycle window after a setpoint change (where the
 startup reduction applies here but is a dead store in pid_ac) this matches
 pid_ac term for term. start_change_temp is seeded from that same first real
 reading rather than pid_ac's fixed 150, so the two also diverge on how much
 integral windup a setpoint change permits before that window ends.

 PID controller based on proportional band in standard PID form
 https://en.wikipedia.org/wiki/PID_controller#Ideal_versus_standard_PID_form
   u = Kp (e(t)+ 1/Ti INT + Td de/dt)
  PB = Proportional Band
  Ti = Goal of eliminating in Ti seconds
  Td = Predicts error value at Td in seconds

  Configuration Defaults:
  "config": {
      "PB": 60.0,
      "Td": 45.0,
      "Ti": 180.0,
      "center": 0.5
   }

*****************************************
"""

"""
Imported Libraries
"""
import json
import math
import threading
import time
from collections import deque
from copy import deepcopy
from dataclasses import asdict, dataclass
from enum import StrEnum
from hashlib import sha256

from common.control_trace import AllocationClampReason, ControllerBranch
from common.learning_trajectory import ModelFitLineage, canonical_trajectory_digest
from common.model_evidence import (
    EvidenceKind,
    ModelEvidenceRecord,
    PidSpFitDecisionEvidence,
)
from common.persistence.learning_trajectory import (
    FitCorpusEmptyError,
    FitCorpusSnapshot,
)
from controller.applied_output import OutputSource
from controller.base import ControllerLearningDiagnostics, PidSpTraceDiagnostics
from controller.fopdt_identifier import FOPDTIdentifier
from controller.model_learning.contracts import CandidateOrigin, FitRequest, FrameObservation
from controller.model_learning.installation_identity import (
    InstallationIdentityProvider,
    InstallationIdentityUnavailable,
    installation_identity_digest,
    os_installation_identity,
)
from controller.model_learning.pid_sp_fitting import (
    PidSpFitStatus,
    fit_pid_sp_corpus,
)
from controller.mpc_allocator import AllocationResult
from controller.pid_base import PIDControllerBase
from controller.pid_sp_delay_evidence import (
    DelayProfile,
    EpisodeAccumulator,
    ExcitationEpisode,
)
from controller.pid_sp_learning import build_pid_sp_live_learning
from controller.pid_sp_model_selection import (
    PID_SP_LEARNING_CHECKPOINT_SCHEMA,
    PID_SP_LEARNING_PREPARE_SCHEMA,
    PID_SP_LEGACY_LEARNING_CHECKPOINT_SCHEMA,
    ModelComparison,
    ModelConfirmation,
    PidSpCheckpoint,
    SelectedPidSpModel,
    compare_model_fits,
    decode_model_confirmation,
    decode_pid_sp_checkpoint,
    encode_model_confirmation,
    encode_pid_sp_checkpoint,
)
from controller.pid_sp_observation import (
    PidSpInterval,
    PidSpObservationDecision,
    PidSpObservationOutcome,
    canonical_pid_sp_observation_model_digest,
)
from controller.smith_predictor import SmithPredictor
from grillplat.actuator_capabilities import AUGER_TIMING

#: Output reduction for the first three cycles after a setpoint change.
STARTUP_REDUCTION = 0.65


class PidSpLearningOutcome(StrEnum):
    DISABLED = "disabled"
    INSUFFICIENT = "insufficient"
    REJECTED = "rejected"
    FAILED = "failed"
    ACCEPTED_NEXT_COOK = "accepted-next-cook"
    CHECKPOINT_FAILURE = "checkpoint-failure"


_LEARNING_CHECKPOINT_SCHEMA = PID_SP_LEARNING_CHECKPOINT_SCHEMA
_LEGACY_LEARNING_CHECKPOINT_SCHEMA = PID_SP_LEGACY_LEARNING_CHECKPOINT_SCHEMA
_LEARNING_PREPARED_CHECKPOINT_SCHEMA = PID_SP_LEARNING_PREPARE_SCHEMA


@dataclass(frozen=True, slots=True)
class _FitIntent:
    ticket: str
    origin: CandidateOrigin
    snapshot: FitCorpusSnapshot | None = None
    terminal_outcome: PidSpLearningOutcome | None = None
    terminal_reason: str | None = None


def _causal_digest(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _to_f(value, units):
    return value if units == "F" else value * 9.0 / 5.0 + 32.0


def _from_f(value, units):
    return value if units == "F" else (value - 32.0) * 5.0 / 9.0


class Controller(PIDControllerBase):
    def __init__(
        self,
        config,
        units,
        cycle_data,
        *,
        logger=None,
        model_persistence=None,
        trajectory_repository=None,
        fit_partition_digest=None,
        clock_ms=None,
        installation_identity_provider: InstallationIdentityProvider = os_installation_identity,
    ):
        super().__init__(config, units, cycle_data)

        self._calculate_gains(config.get("PB", 60.0), config.get("Ti", 180.0), config.get("Td", 45.0))

        self.p = 0.0
        self.i = 0.0
        self.d = 0.0
        self.u = 0

        self.pb = config.get("PB", 60.0)

        self.units = units

        self.last_update = time.time()
        self.last_set_time = time.time()
        self.error = 0.0
        self.set_point = 0

        self.center = 0.5
        self.center_factor = config.get("center_factor", 0.0010)
        # `center` serves two roles that are not the same thing: the output the
        # loop sits at when the error is zero, and the authority the integral is
        # allowed. Only the first is the operating point, and only the first is
        # something the identifier can supply, so they are tracked apart.
        # Until a model can name the operating point this stays the heuristic,
        # which is what the controller has always used for both.
        self.feed_forward = self.center
        self._integral_seeded = False

        self.stable_window = config.get("stable_window", 12)
        # Three control cycles is what the guards below mean. The control cycle
        # is the auger's pulse frame: Hold paces the auger from PulseScheduler's
        # timing, which takes no setting.
        self.cycle_time = AUGER_TIMING.frame_s

        # Off is the negative control every measurement of this needs, not a
        # user-facing choice: the identified operating point beats the heuristic
        # at every set point measured.
        self.bias_from_model = config.get("bias_from_model", True)

        self.derv = 0.0
        self.inter = 0.0

        # Seeded from the first observed temperature, so the first derivative
        # is exactly zero rather than measured against a value that was never
        # sampled. start_change_temp inherits the same None until set_target()
        # copies it in, which happens immediately below.
        self.last = None
        self.start_change_temp = None
        self.new_target = False

        self._trace_diagnostics = None
        self._trace_allocation: AllocationResult | None = None
        self.identifier = FOPDTIdentifier()
        self.predictor = SmithPredictor()
        self._delay_episode_accumulator = EpisodeAccumulator()
        self._delay_profile: DelayProfile | None = None
        self._selected = None
        self._model_confirmation = ModelConfirmation()
        self._model_comparison: ModelComparison | None = None
        self._active_selected_model: SelectedPidSpModel | None = None
        self._model_revision = 0
        self._persistence_revision = 0
        self._model_provenance = "online-common-validation"
        self._learning_enabled = bool(config.get("enable_identification", True))
        self._learning_configuration = deepcopy(config)
        try:
            self._installation_identity_digest = installation_identity_digest(installation_identity_provider)
        except InstallationIdentityUnavailable:
            self._installation_identity_digest = None
        self._model_persistence = model_persistence
        self._trajectory_repository = trajectory_repository
        self._fit_partition_digest = fit_partition_digest
        self._clock_ms = (lambda: time.time_ns() // 1_000_000) if clock_ms is None else clock_ms
        self._learning_session_id = "runtime"
        self._learning_cook_id: str | None = None
        self._learning_role_generation = 0
        self._fit_lock = threading.Lock()
        self._fit_intents: deque[_FitIntent] = deque()
        self._terminal_fit_tickets: dict[str, PidSpLearningOutcome] = {}
        self._fit_thread: threading.Thread | None = None
        self._last_fit_outcome = PidSpLearningOutcome.DISABLED
        self._last_fit_reason = "not-scheduled"
        self._active_fit_request: FitRequest | None = None
        self._durable_confirmation_identity: tuple[str, str, str] | None = None
        self._restore_revalidation_candidate: PidSpCheckpoint | None = None

        self.set_target(0.0)

    # ------------------------------------------------------------ capabilities
    def set_output(self, applied):
        """Treat progress/output feedback as telemetry; completed frames own learning."""
        del applied

    def observe_frame(self, observation: FrameObservation):
        interval = PidSpInterval(
            start_s=observation.frame_start_s,
            end_s=observation.frame_end_s,
            temperature_f=_to_f(observation.temp_c, "C"),
            realized_duty=float(observation.realized_auger_duty),
            continuous=observation.continuous,
            observation_sequence=observation.observation_sequence,
            role_generation=observation.role_generation,
        )
        governing_model = self.predictor.governing_model()
        if not observation.probe_valid:
            decision = PidSpObservationDecision.INVALID_PROBE
        elif observation.output_source != OutputSource.CONTROLLER.value:
            decision = PidSpObservationDecision.NON_CONTROLLER_OUTPUT
        elif not interval.continuous:
            decision = PidSpObservationDecision.DISCONTINUOUS
        elif (
            observation.lid_open
            or observation.safety_inhibited
            or observation.manual_override
            or observation.stale
            or observation.skipped
            or observation.reset
        ):
            decision = PidSpObservationDecision.INHIBITED
        else:
            self.predictor.record_interval(
                interval.start_s,
                interval.end_s,
                interval.realized_duty,
            )
            self.identifier.observe_interval(
                interval.start_s,
                interval.end_s,
                interval.realized_duty,
                interval.temperature_f,
            )
            self._delay_episode_accumulator.observe(interval)
            decision = PidSpObservationDecision.ACCEPTED
        if decision is not PidSpObservationDecision.ACCEPTED:
            self._delay_episode_accumulator.interrupt(decision.value)
        status = self.identifier.status()
        duty_segments = int(status["duty_segments"])
        return PidSpObservationOutcome(
            decision=decision,
            effective_updates=(1 if decision is PidSpObservationDecision.ACCEPTED else 0),
            duty_variance=float(status["duty_std"]) ** 2,
            duty_levels=(2 if status["transition_seen"] else min(duty_segments, 1)),
            role_generation=interval.role_generation,
            model_digest=(
                canonical_pid_sp_observation_model_digest(governing_model)
                if decision is PidSpObservationDecision.ACCEPTED
                else None
            ),
        ).as_runner_outcome()

    def observation_failure(
        self,
        observation: FrameObservation,
        error: BaseException,
    ):
        del observation, error

    def completed_excitation_episodes(self) -> tuple[ExcitationEpisode, ...]:
        return self._delay_episode_accumulator.completed()

    def bind_learning_identity(
        self,
        session_id: str,
        cook_id: str | None,
        role_generation: int,
    ) -> None:
        self._learning_session_id = session_id
        self._learning_cook_id = cook_id
        self._learning_role_generation = role_generation

    def _current_incumbent_digest(self) -> str:
        selected = self._active_selected_model
        return canonical_pid_sp_observation_model_digest(None) if selected is None else selected.model_digest

    def _fit_ticket(
        self,
        origin: CandidateOrigin,
        fit_corpus_digest: str | None = None,
        *,
        terminal_outcome: PidSpLearningOutcome | None = None,
        terminal_reason: str | None = None,
    ) -> str:
        return _causal_digest(
            {
                "controller": "pid_sp",
                "purpose": "corpus-fit-ticket/v1",
                "session_id": self._learning_session_id,
                "cook_id": self._learning_cook_id,
                "role_generation": self._learning_role_generation,
                "origin": origin.value,
                "fit_corpus_digest": fit_corpus_digest,
                "terminal_outcome": (None if terminal_outcome is None else terminal_outcome.value),
                "terminal_reason": terminal_reason,
                "configuration_digest": canonical_trajectory_digest(self._learning_configuration),
                "parent_incumbent_digest": self._current_incumbent_digest(),
            }
        )

    def _record_unattributed_terminal(
        self,
        ticket: str,
        origin: CandidateOrigin,
        outcome: PidSpLearningOutcome,
        reason: str,
    ) -> None:
        configuration_digest = canonical_trajectory_digest(self._learning_configuration)
        incumbent_digest = self._current_incumbent_digest()
        evidence_id = _causal_digest(
            {
                "controller": "pid_sp",
                "request_id": ticket,
                "request_bound": False,
                "configuration_digest": configuration_digest,
                "parent_incumbent_digest": incumbent_digest,
                "outcome": outcome.value,
                "reason": reason,
            }
        )
        record = ModelEvidenceRecord(
            evidence_id=evidence_id,
            kind=EvidenceKind.PID_SP_FIT_DECISION,
            session_id=self._learning_session_id,
            cook_id=self._learning_cook_id,
            timestamp_ms=self._clock_ms(),
            role_generation=self._learning_role_generation,
            model_digest=incumbent_digest,
            provenance_digest=None,
            payload=PidSpFitDecisionEvidence(
                request_id=ticket,
                controller="pid_sp",
                origin=origin.value,
                outcome=outcome.value,
                reason=reason,
                request_bound=False,
                fit_corpus_digest=None,
                configuration_digest=configuration_digest,
                selected_form=None,
                candidate_digest=None,
                parent_incumbent_digest=incumbent_digest,
                parent_incumbent_generation=None,
                confirmation_candidate_digest=None,
                candidate_generation=None,
                confirmation_observed=0,
            ),
        )
        self._submit_decision_evidence(record)

    def _run_unattributed_terminal(
        self,
        ticket: str,
        origin: CandidateOrigin,
        outcome: PidSpLearningOutcome,
        reason: str,
    ) -> None:
        try:
            self._record_unattributed_terminal(
                ticket,
                origin,
                outcome,
                reason,
            )
            persistence = self._model_persistence
            if persistence is not None:
                persistence.barrier(timeout=2.0)
        finally:
            with self._fit_lock:
                self._terminal_fit_tickets[ticket] = outcome

    def record_corpus_fit_disabled(
        self,
        origin: CandidateOrigin,
        reason: str,
    ) -> bool:
        if not isinstance(origin, CandidateOrigin):
            raise TypeError("origin must be a CandidateOrigin")
        if not isinstance(reason, str) or not reason:
            raise ValueError("disabled fit reason must be nonblank")
        return self._queue_pre_request_terminal(
            origin,
            PidSpLearningOutcome.DISABLED,
            reason,
        )

    def record_corpus_fit_failed(
        self,
        origin: CandidateOrigin,
        reason: str,
    ) -> bool:
        if not isinstance(origin, CandidateOrigin):
            raise TypeError("origin must be a CandidateOrigin")
        if not isinstance(reason, str) or not reason:
            raise ValueError("failed fit reason must be nonblank")
        return self._queue_pre_request_terminal(
            origin,
            PidSpLearningOutcome.FAILED,
            reason,
        )

    def _queue_pre_request_terminal(
        self,
        origin: CandidateOrigin,
        outcome: PidSpLearningOutcome,
        reason: str,
    ) -> bool:
        if self._model_persistence is None:
            return False
        ticket = self._fit_ticket(
            origin,
            terminal_outcome=outcome,
            terminal_reason=reason,
        )
        with self._fit_lock:
            self._last_fit_outcome = outcome
            self._last_fit_reason = reason
            self._fit_intents.append(
                _FitIntent(
                    ticket=ticket,
                    origin=origin,
                    terminal_outcome=outcome,
                    terminal_reason=reason,
                )
            )
            self._start_fit_worker_locked()
        return True

    def _start_fit_worker_locked(self) -> None:
        if self._fit_thread is not None and self._fit_thread.is_alive():
            return
        self._fit_thread = threading.Thread(
            target=self._run_scheduled_fit,
            name="pid-sp-corpus-fit",
            daemon=True,
        )
        self._fit_thread.start()

    def _schedule_corpus_fit_ticket(
        self,
        origin: CandidateOrigin,
    ) -> str | None:
        if not isinstance(origin, CandidateOrigin):
            raise TypeError("origin must be a CandidateOrigin")
        if (
            not self._learning_enabled
            or self._trajectory_repository is None
            or self._fit_partition_digest is None
            or self._model_persistence is None
        ):
            self.record_corpus_fit_disabled(
                origin,
                "learning-dependencies-unavailable",
            )
            return None
        partition = self._fit_partition_digest()
        if partition is None:
            self.record_corpus_fit_disabled(
                origin,
                "fit-partition-unavailable",
            )
            return None
        try:
            snapshot = self._trajectory_repository.snapshot_fit_corpus(partition)
        except Exception as error:
            empty = isinstance(error, FitCorpusEmptyError)
            outcome = PidSpLearningOutcome.INSUFFICIENT if empty else PidSpLearningOutcome.FAILED
            reason = "fit-corpus-empty" if empty else f"fit-corpus-snapshot-failed:{type(error).__name__}: {error}"
            ticket = self._fit_ticket(
                origin,
                terminal_outcome=outcome,
                terminal_reason=reason,
            )
            with self._fit_lock:
                self._fit_intents.append(
                    _FitIntent(
                        ticket=ticket,
                        origin=origin,
                        terminal_outcome=outcome,
                        terminal_reason=reason,
                    )
                )
            return ticket
        ticket = self._fit_ticket(
            origin,
            snapshot.identity.corpus_digest,
        )
        with self._fit_lock:
            self._fit_intents.append(
                _FitIntent(
                    ticket=ticket,
                    origin=origin,
                    snapshot=snapshot,
                )
            )
            return ticket

    def schedule_corpus_fit(self, origin: CandidateOrigin) -> bool:
        ticket = self._schedule_corpus_fit_ticket(origin)
        if ticket is None:
            return False
        with self._fit_lock:
            self._start_fit_worker_locked()
        return True

    def _run_scheduled_fit(self) -> None:
        while True:
            if self.poll_learning_off_path() is None:
                with self._fit_lock:
                    if self._fit_intents:
                        continue
                    self._fit_thread = None
                return

    def _pending_learning_checkpoint(
        self,
        identity: tuple[str, str, str],
        *,
        revision: int | None = None,
    ) -> dict[str, object]:
        incumbent = (
            None
            if self._active_selected_model is None
            else encode_pid_sp_checkpoint(
                self._active_selected_model,
                revision=self._model_revision,
                provenance=self._model_provenance,
                installation_identity_digest=self._installation_identity_digest,
            )
        )
        checkpoint_revision = self._persistence_revision if revision is None else revision
        return {
            "schema": _LEARNING_CHECKPOINT_SCHEMA,
            "revision": checkpoint_revision,
            "confirmation": encode_model_confirmation(self._model_confirmation),
            "identity": {
                "fit_corpus_digest": identity[0],
                "configuration_digest": identity[1],
                "incumbent_digest": identity[2],
            },
            "installation_identity_digest": self._installation_identity_digest,
            "incumbent": incumbent,
        }

    @staticmethod
    def _prepared_learning_checkpoint(
        *,
        revision: int,
        terminal: ModelEvidenceRecord,
        proposed: dict[str, object],
        incumbent: dict[str, object] | None,
    ) -> dict[str, object]:
        payload = terminal.payload
        if not isinstance(payload, PidSpFitDecisionEvidence):
            raise TypeError("prepared terminal must be PID-SP fit evidence")
        lineage = {
            "request_id": payload.request_id,
            "candidate_digest": payload.candidate_digest,
            "confirmation_candidate_digest": (payload.confirmation_candidate_digest),
            "fit_corpus_digest": payload.fit_corpus_digest,
            "configuration_digest": payload.configuration_digest,
            "parent_incumbent_digest": payload.parent_incumbent_digest,
            "parent_incumbent_generation": payload.parent_incumbent_generation,
            "candidate_generation": payload.candidate_generation,
        }
        return {
            "schema": _LEARNING_PREPARED_CHECKPOINT_SCHEMA,
            "revision": revision,
            "terminal_evidence_json": terminal.model_dump_json(),
            "proposed": {
                "checkpoint": deepcopy(proposed),
                "lineage": lineage,
            },
            "incumbent": deepcopy(incumbent),
        }

    def _decision_evidence(
        self,
        request: FitRequest,
        *,
        selected: SelectedPidSpModel | None,
        outcome: PidSpLearningOutcome,
        reason: str,
        episode_ids: tuple[str, ...] = (),
        confirmation_candidate_digest: str | None = None,
    ) -> ModelEvidenceRecord:
        incumbent_digest = request.parent_incumbent_digest
        model_digest = incumbent_digest if selected is None else selected.model_digest
        evidence_id = _causal_digest(
            {
                "controller": "pid_sp",
                "request_id": request.request_id,
                "fit_corpus_digest": request.fit_corpus.corpus_digest,
                "configuration_digest": request.configuration_digest,
                "parent_incumbent_digest": request.parent_incumbent_digest,
                "candidate_digest": (None if selected is None else selected.model_digest),
                "confirmation_candidate_digest": (confirmation_candidate_digest),
                "outcome": outcome.value,
                "reason": reason,
            }
        )
        return ModelEvidenceRecord(
            evidence_id=evidence_id,
            kind=EvidenceKind.PID_SP_FIT_DECISION,
            session_id=self._learning_session_id,
            cook_id=self._learning_cook_id,
            timestamp_ms=self._clock_ms(),
            role_generation=self._learning_role_generation,
            model_digest=model_digest,
            provenance_digest=request.fit_corpus.corpus_digest,
            payload=PidSpFitDecisionEvidence(
                request_id=request.request_id,
                controller="pid_sp",
                origin=request.origin.value,
                outcome=outcome.value,
                confirmation_candidate_digest=(None if selected is None else confirmation_candidate_digest),
                reason=reason,
                request_bound=True,
                fit_corpus_digest=request.fit_corpus.corpus_digest,
                configuration_digest=request.configuration_digest,
                selected_form=(None if selected is None else selected.form.value),
                candidate_digest=(None if selected is None else selected.model_digest),
                parent_incumbent_digest=request.parent_incumbent_digest,
                parent_incumbent_generation=request.parent_incumbent_generation,
                candidate_generation=request.candidate_generation,
                confirmation_observed=(0 if selected is None else selected.confirmation_observed),
                episode_ids=episode_ids,
            ),
        )

    def _submit_decision_evidence(self, record: ModelEvidenceRecord) -> bool:
        persistence = self._model_persistence
        if persistence is None:
            return False
        submit = getattr(persistence, "submit_evidence", None)
        if callable(submit):
            return bool(submit(record).accepted)
        submission = persistence.submit_evidence_batch((record,))
        return bool(submission.accepted)

    def _record_checkpoint_failure(
        self,
        request: FitRequest,
        selected: SelectedPidSpModel,
        reason: str,
        episode_ids: tuple[str, ...],
        confirmation_candidate_digest: str,
    ) -> None:
        record = self._decision_evidence(
            request,
            selected=selected,
            outcome=PidSpLearningOutcome.CHECKPOINT_FAILURE,
            reason=reason,
            episode_ids=episode_ids,
            confirmation_candidate_digest=confirmation_candidate_digest,
        )
        if self._submit_decision_evidence(record):
            persistence = self._model_persistence
            if persistence is not None:
                persistence.barrier(timeout=2.0)

    def _persist_checkpoint_terminal(
        self,
        prepared: dict[str, object],
        committed: dict[str, object],
        success: ModelEvidenceRecord,
        failure: ModelEvidenceRecord,
    ) -> tuple[bool, bool]:
        persistence = self._model_persistence
        if persistence is None:
            return False, False
        submit_compound = getattr(
            persistence,
            "submit_checkpoint_with_terminal_evidence",
            None,
        )
        if not callable(submit_compound):
            return False, False
        receipt = submit_compound(
            "pid_sp",
            prepared,
            committed,
            success,
            failure,
        )
        if not receipt.accepted:
            return False, False
        durable = bool(receipt.wait() and receipt.completed and receipt.durable)
        return durable, not durable

    def _persist_restore_revalidation_retirement(
        self,
        request: FitRequest,
    ) -> bool:
        staged = self._restore_revalidation_candidate
        if staged is None:
            return True
        persistence = self._model_persistence
        submit = getattr(persistence, "submit_durable_checkpoint", None)
        if not callable(submit):
            return False
        revision = max(self._persistence_revision, staged.revision) + 1
        identity = (
            request.fit_corpus.corpus_digest,
            request.configuration_digest,
            request.parent_incumbent_digest,
        )
        retired = self._pending_learning_checkpoint(identity, revision=revision)
        retired["confirmation"] = encode_model_confirmation(ModelConfirmation())
        try:
            receipt = submit("pid_sp", retired)
            durable = bool(receipt.accepted and receipt.wait() and receipt.completed and receipt.durable)
        except Exception:
            durable = False
        if not durable:
            return False
        self._persistence_revision = revision
        self._model_confirmation.reset()
        self._durable_confirmation_identity = identity
        self._restore_revalidation_candidate = None
        return True

    def _complete_fit_run(
        self,
        request: FitRequest,
        *,
        selected: SelectedPidSpModel | None,
        reason: str,
    ) -> bool:
        try:
            self._trajectory_repository.complete_fit(
                request.request_id,
                candidate_digest=(None if selected is None else selected.model_digest),
                error=(reason if selected is None else None),
            )
        except Exception:
            return False
        return True

    def _persist_non_candidate_outcome(
        self,
        request: FitRequest,
        *,
        outcome: PidSpLearningOutcome,
        reason: str,
        episode_ids: tuple[str, ...] = (),
    ) -> PidSpLearningOutcome:
        completed = self._complete_fit_run(
            request,
            selected=None,
            reason=reason,
        )
        terminal_outcome = outcome if completed else PidSpLearningOutcome.CHECKPOINT_FAILURE
        terminal_reason = reason if completed else "fit-run-persistence-failed"
        record = self._decision_evidence(
            request,
            selected=None,
            outcome=terminal_outcome,
            reason=terminal_reason,
            episode_ids=episode_ids,
        )
        persisted = self._submit_decision_evidence(record)
        persistence = self._model_persistence
        durable = persisted and persistence is not None and persistence.barrier(timeout=2.0)
        if durable:
            return terminal_outcome
        self._last_fit_reason = "terminal-outcome-persistence-failed"
        return PidSpLearningOutcome.CHECKPOINT_FAILURE

    def _evaluate_fit_request(
        self,
        ticket: str,
        origin: CandidateOrigin,
        snapshot: FitCorpusSnapshot,
    ) -> PidSpLearningOutcome:
        configuration_digest = canonical_trajectory_digest(self._learning_configuration)
        incumbent_digest = self._current_incumbent_digest()
        parent_generation = self._model_revision
        candidate_generation = parent_generation + 1
        request_id = _causal_digest(
            {
                "controller": "pid_sp",
                "purpose": "corpus-fit-request/v1",
                "session_id": self._learning_session_id,
                "cook_id": self._learning_cook_id,
                "role_generation": self._learning_role_generation,
                "origin": origin.value,
                "fit_corpus_digest": snapshot.identity.corpus_digest,
                "configuration_digest": configuration_digest,
                "parent_incumbent_digest": incumbent_digest,
                "parent_incumbent_generation": parent_generation,
                "candidate_generation": candidate_generation,
            }
        )
        request = FitRequest(
            request_id=request_id,
            origin=origin,
            fit_corpus=snapshot.identity,
            configuration_digest=configuration_digest,
            parent_incumbent_digest=incumbent_digest,
            parent_incumbent_generation=parent_generation,
            candidate_generation=candidate_generation,
        )
        self._trajectory_repository.record_fit_request(
            snapshot,
            ModelFitLineage(
                request_id=request.request_id,
                parent_incumbent_digest=request.parent_incumbent_digest,
                parent_incumbent_generation=request.parent_incumbent_generation,
                candidate_generation=request.candidate_generation,
                fit_corpus=request.fit_corpus,
                fit_corpus_digest=request.fit_corpus.corpus_digest,
                trigger_origin=request.origin.value,
                result_status="running",
                candidate_digest=None,
            ),
        )
        self._active_fit_request = request
        result = fit_pid_sp_corpus(
            request,
            snapshot.segments,
            self._learning_configuration,
        )
        episode_ids = tuple(episode.episode_id for episode in result.episodes)
        if result.status is PidSpFitStatus.FAILED:
            self._last_fit_reason = result.reason
            return self._persist_non_candidate_outcome(
                request,
                outcome=PidSpLearningOutcome.FAILED,
                reason=result.reason,
                episode_ids=episode_ids,
            )
        if result.status is PidSpFitStatus.INSUFFICIENT:
            self._last_fit_reason = result.reason
            return self._persist_non_candidate_outcome(
                request,
                outcome=PidSpLearningOutcome.INSUFFICIENT,
                reason=result.reason,
                episode_ids=episode_ids,
            )
        comparison = result.comparison
        if result.delay_profiles and comparison is not None and comparison.fits:
            selected_form = (
                comparison.selected.form
                if comparison.selected is not None
                else min(
                    comparison.fits,
                    key=lambda fit: fit.mean_validation_loss,
                ).form
            )
            self._delay_profile = next(
                (profile for profile in result.delay_profiles if profile.model_form == selected_form.value),
                self._delay_profile,
            )
        if comparison is None or comparison.selected is None:
            self._last_fit_reason = result.reason
            outcome = self._persist_non_candidate_outcome(
                request,
                outcome=PidSpLearningOutcome.REJECTED,
                reason=result.reason,
                episode_ids=episode_ids,
            )
            if outcome is PidSpLearningOutcome.REJECTED and not self._persist_restore_revalidation_retirement(request):
                self._last_fit_reason = "restore-revalidation-retirement-failed"
                return PidSpLearningOutcome.CHECKPOINT_FAILURE
            return outcome

        incumbent_checkpoint = self.get_model_snapshot()
        normalized = compare_model_fits(
            comparison.fits,
            fit_corpus_digest=request.fit_corpus.corpus_digest,
            configuration_digest=request.configuration_digest,
        )
        identity = (
            request.fit_corpus.corpus_digest,
            request.configuration_digest,
            request.parent_incumbent_digest,
        )
        if identity != self._durable_confirmation_identity:
            self._model_confirmation.reset()
        previous_confirmation = self._model_confirmation.snapshot()
        confirmed = self.accept_model_comparison(normalized, activate=False)
        selected = confirmed.selected
        assert selected is not None
        (
            attempted_confirmation_key,
            attempted_confirmation_observed,
        ) = self._model_confirmation.snapshot()
        if attempted_confirmation_key is None or attempted_confirmation_observed != selected.confirmation_observed:
            raise RuntimeError("attempted confirmation identity is inconsistent")
        outcome = PidSpLearningOutcome.ACCEPTED_NEXT_COOK if confirmed.authorized else PidSpLearningOutcome.REJECTED
        reason = (
            "authorized-for-next-cook"
            if confirmed.authorized
            else (f"confirmation-pending:{selected.confirmation_observed}/" + f"{selected.confirmation_required}")
        )
        checkpoint_revision = self._persistence_revision + 2
        checkpoint = (
            encode_pid_sp_checkpoint(
                selected,
                revision=checkpoint_revision,
                provenance="persistent-corpus-common-validation",
                installation_identity_digest=self._installation_identity_digest,
            )
            if confirmed.authorized
            else self._pending_learning_checkpoint(
                identity,
                revision=checkpoint_revision,
            )
        )
        persistence = self._model_persistence
        evidence_blocked = bool(persistence is None or getattr(persistence, "evidence_blocked", False))
        if evidence_blocked:
            self._model_confirmation.restore(*previous_confirmation)
            self._last_fit_reason = "comparison-evidence-rejected"
            self._complete_fit_run(
                request,
                selected=None,
                reason=self._last_fit_reason,
            )
            self._record_checkpoint_failure(
                request,
                selected,
                self._last_fit_reason,
                episode_ids,
                attempted_confirmation_key,
            )
            return PidSpLearningOutcome.CHECKPOINT_FAILURE
        if not self._complete_fit_run(
            request,
            selected=selected,
            reason=reason,
        ):
            self._model_confirmation.restore(*previous_confirmation)
            self._last_fit_reason = "fit-run-persistence-failed"
            self._record_checkpoint_failure(
                request,
                selected,
                self._last_fit_reason,
                episode_ids,
                attempted_confirmation_key,
            )
            return PidSpLearningOutcome.CHECKPOINT_FAILURE
        success_record = self._decision_evidence(
            request,
            selected=selected,
            outcome=outcome,
            reason=reason,
            episode_ids=episode_ids,
            confirmation_candidate_digest=attempted_confirmation_key,
        )
        failure_reason = "checkpoint-or-terminal-persistence-failed"
        failure_record = self._decision_evidence(
            request,
            selected=selected,
            outcome=PidSpLearningOutcome.CHECKPOINT_FAILURE,
            reason=failure_reason,
            episode_ids=episode_ids,
            confirmation_candidate_digest=attempted_confirmation_key,
        )
        prepared = self._prepared_learning_checkpoint(
            revision=checkpoint_revision - 1,
            terminal=success_record,
            proposed=checkpoint,
            incumbent=incumbent_checkpoint,
        )
        durable, failure_recorded = self._persist_checkpoint_terminal(
            prepared,
            checkpoint,
            success_record,
            failure_record,
        )
        if not durable:
            self._model_confirmation.restore(*previous_confirmation)
            self._last_fit_reason = failure_reason
            self._persistence_revision = checkpoint_revision - 1
            if not failure_recorded:
                self._record_checkpoint_failure(
                    request,
                    selected,
                    self._last_fit_reason,
                    episode_ids,
                    attempted_confirmation_key,
                )
            return PidSpLearningOutcome.CHECKPOINT_FAILURE
        self._persistence_revision = checkpoint_revision
        self._durable_confirmation_identity = None if confirmed.authorized else identity
        if confirmed.authorized and self._restore_revalidation_candidate is not None:
            self._activate_selected(
                selected,
                revision=checkpoint_revision,
                provenance="persistent-corpus-common-validation",
            )
            self._restore_revalidation_candidate = None
        self._model_comparison = confirmed
        self._last_fit_reason = reason
        return outcome

    def poll_learning_off_path(
        self,
        *,
        live_origin: CandidateOrigin | None = None,
    ) -> PidSpLearningOutcome | None:
        del live_origin
        with self._fit_lock:
            if not self._fit_intents:
                return None
            intent = self._fit_intents.popleft()
        ticket = intent.ticket
        origin = intent.origin
        snapshot = intent.snapshot
        if intent.terminal_outcome is not None:
            reason = intent.terminal_reason
            assert reason is not None
            self._run_unattributed_terminal(
                ticket,
                origin,
                intent.terminal_outcome,
                reason,
            )
            return intent.terminal_outcome
        assert snapshot is not None
        try:
            outcome = self._evaluate_fit_request(ticket, origin, snapshot)
        except Exception as error:
            outcome = PidSpLearningOutcome.FAILED
            self._last_fit_reason = f"{type(error).__name__}: {error}"
            request = self._active_fit_request
            if request is not None:
                outcome = self._persist_non_candidate_outcome(
                    request,
                    outcome=PidSpLearningOutcome.FAILED,
                    reason=self._last_fit_reason,
                )
            else:
                self._record_unattributed_terminal(
                    ticket,
                    origin,
                    outcome,
                    self._last_fit_reason,
                )
        finally:
            self._active_fit_request = None
        with self._fit_lock:
            self._terminal_fit_tickets[ticket] = outcome
            self._last_fit_outcome = outcome
        return outcome

    def _consume_terminal_corpus_fit_ticket(
        self,
        ticket: str,
        origin: CandidateOrigin,
    ) -> bool:
        del origin
        with self._fit_lock:
            return self._terminal_fit_tickets.pop(ticket, None) is not None

    def fail_corpus_fit(
        self,
        ticket: str,
        error: BaseException | str,
    ) -> None:
        detail = error if isinstance(error, str) else f"{type(error).__name__}: {error}"
        with self._fit_lock:
            self._last_fit_reason = detail
            self._last_fit_outcome = PidSpLearningOutcome.FAILED
            self._terminal_fit_tickets[ticket] = PidSpLearningOutcome.FAILED

    def close(self) -> None:
        with self._fit_lock:
            thread = self._fit_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join()

    def get_learning_diagnostics(self) -> ControllerLearningDiagnostics:
        episodes = self.completed_excitation_episodes()
        return ControllerLearningDiagnostics(
            schema_version=1,
            state=build_pid_sp_live_learning(
                self.identifier.status(),
                self.predictor.status(),
                completed_episode_count=len(episodes),
                delay_profile=self._delay_profile,
                comparison=self._model_comparison,
                active_selected=self._active_selected_model,
            ),
        )

    def _build_status(self, diagnostics):
        learning = diagnostics.as_json()
        return {
            "p": self.p,
            "i": self.i,
            "d": self.d,
            "u": self.u,
            "error": self.error,
            "set_point": self.set_point,
            "center": self.center,
            "feed_forward": self.feed_forward,
            "selected_temp": self._selected,
            "last_selected": self.last,
            "identifier": learning["identifier"],
            "predictor": learning["predictor"],
            "learning": learning,
        }

    @staticmethod
    def _predictor_model(
        selected: SelectedPidSpModel,
        revision: int,
    ) -> dict[str, float | int | str]:
        return {
            "form": selected.form.value,
            **asdict(selected.parameters),
            "revision": revision,
            "basin_upper_s": max(
                selected.delay_basin.upper_s,
                selected.delay_basin.confidence_upper_s,
            ),
        }

    def _activate_selected(
        self,
        selected: SelectedPidSpModel,
        *,
        revision: int,
        provenance: str,
    ) -> None:
        model = self._predictor_model(selected, revision)
        if not self.identifier.preflight_restore(model):
            raise ValueError("selected model failed identifier preflight")
        if not self.predictor.preflight_trust(
            model,
            authority_digest=selected.model_digest,
        ):
            raise ValueError("selected model failed predictor preflight")
        if not self.identifier.restore(model):
            raise ValueError("selected model failed identifier activation")
        if not self.predictor.trust(
            model,
            authority_digest=selected.model_digest,
        ):
            raise ValueError("selected model failed predictor activation")
        self._active_selected_model = selected
        self._model_revision = revision
        self._persistence_revision = max(
            self._persistence_revision,
            revision,
        )
        self._model_provenance = provenance

    def accept_model_comparison(
        self,
        comparison: ModelComparison,
        *,
        activate: bool = True,
    ) -> ModelComparison:
        if not isinstance(comparison, ModelComparison):
            raise TypeError("comparison must be a complete ModelComparison")
        if type(activate) is not bool:
            raise TypeError("activate must be a bool")
        confirmed = compare_model_fits(
            comparison.fits,
            fit_corpus_digest=comparison.fit_corpus_digest,
            configuration_digest=comparison.configuration_digest,
            confirmation=self._model_confirmation,
        )
        selected = confirmed.selected
        current_configuration = confirmed.configuration_digest == canonical_trajectory_digest(
            self._learning_configuration
        )
        if current_configuration and self._restore_revalidation_candidate is not None and selected is None:
            self._restore_revalidation_candidate = None
        if (
            activate
            and confirmed.authorized
            and selected is not None
            and (
                self._active_selected_model is None or selected.model_digest != self._active_selected_model.model_digest
            )
        ):
            self._activate_selected(
                selected,
                revision=self._model_revision + 1,
                provenance="online-common-validation",
            )
            if current_configuration:
                self._restore_revalidation_candidate = None
        self._model_comparison = confirmed
        return confirmed

    def get_model_snapshot(self):
        identity = self._durable_confirmation_identity
        if identity is not None:
            return self._pending_learning_checkpoint(identity)
        selected = self._active_selected_model
        if selected is None:
            return None
        return encode_pid_sp_checkpoint(
            selected,
            revision=self._model_revision,
            provenance=self._model_provenance,
            installation_identity_digest=self._installation_identity_digest,
        )

    def _abort_prepared_checkpoint(
        self,
        snapshot: dict[str, object],
    ) -> bool:
        incumbent = snapshot.get("incumbent")
        if incumbent is None:
            return True
        if not isinstance(incumbent, dict):
            return False
        return self.restore_model(incumbent)

    @staticmethod
    def _prepared_proposal_checkpoint(
        proposed: dict[str, object],
        terminal: ModelEvidenceRecord,
    ) -> dict[str, object] | None:
        if set(proposed) != {"checkpoint", "lineage"}:
            return None
        checkpoint = proposed["checkpoint"]
        lineage = proposed["lineage"]
        payload = terminal.payload
        if (
            not isinstance(checkpoint, dict)
            or not isinstance(lineage, dict)
            or not isinstance(payload, PidSpFitDecisionEvidence)
            or not payload.request_bound
            or payload.candidate_digest is None
            or payload.confirmation_candidate_digest is None
            or payload.fit_corpus_digest is None
            or payload.parent_incumbent_generation is None
            or payload.candidate_generation is None
        ):
            return None
        expected_lineage = {
            "request_id": payload.request_id,
            "candidate_digest": payload.candidate_digest,
            "confirmation_candidate_digest": (payload.confirmation_candidate_digest),
            "fit_corpus_digest": payload.fit_corpus_digest,
            "configuration_digest": payload.configuration_digest,
            "parent_incumbent_digest": payload.parent_incumbent_digest,
            "parent_incumbent_generation": payload.parent_incumbent_generation,
            "candidate_generation": payload.candidate_generation,
        }
        if lineage != expected_lineage or terminal.model_digest != payload.candidate_digest:
            return None
        if checkpoint.get("schema") in {
            _LEARNING_CHECKPOINT_SCHEMA,
            _LEGACY_LEARNING_CHECKPOINT_SCHEMA,
        }:
            identity = checkpoint.get("identity")
            confirmation = checkpoint.get("confirmation")
            if (
                payload.outcome != PidSpLearningOutcome.REJECTED.value
                or not isinstance(identity, dict)
                or identity
                != {
                    "fit_corpus_digest": payload.fit_corpus_digest,
                    "configuration_digest": payload.configuration_digest,
                    "incumbent_digest": payload.parent_incumbent_digest,
                }
                or not isinstance(confirmation, dict)
            ):
                return None
            try:
                restored_confirmation = decode_model_confirmation(confirmation)
            except KeyError, TypeError, ValueError:
                return None
            candidate_key, observed = restored_confirmation.snapshot()
            if candidate_key != payload.confirmation_candidate_digest or observed != payload.confirmation_observed:
                return None
            return checkpoint
        try:
            decoded = decode_pid_sp_checkpoint(checkpoint)
        except KeyError, TypeError, ValueError:
            return None
        selected = decoded.selected
        if (
            payload.outcome != PidSpLearningOutcome.ACCEPTED_NEXT_COOK.value
            or selected.model_digest != payload.candidate_digest
            or selected.fit_corpus_digest != payload.fit_corpus_digest
            or selected.configuration_digest != payload.configuration_digest
            or selected.form.value != payload.selected_form
            or selected.confirmation_observed != payload.confirmation_observed
        ):
            return None
        return checkpoint

    def _restore_prepared_checkpoint(
        self,
        snapshot: dict[str, object],
    ) -> bool:
        revision = snapshot.get("revision")
        if type(revision) is not int or revision < 0:
            return self._abort_prepared_checkpoint(snapshot)
        if set(snapshot) != {
            "schema",
            "revision",
            "terminal_evidence_json",
            "proposed",
            "incumbent",
        }:
            restored = self._abort_prepared_checkpoint(snapshot)
            self._persistence_revision = max(
                self._persistence_revision,
                revision,
            )
            return restored
        terminal_json = snapshot["terminal_evidence_json"]
        proposed = snapshot["proposed"]
        if not isinstance(terminal_json, str) or not isinstance(proposed, dict):
            restored = self._abort_prepared_checkpoint(snapshot)
            self._persistence_revision = max(
                self._persistence_revision,
                revision,
            )
            return restored
        proposed_checkpoint: dict[str, object] | None = None
        try:
            terminal = ModelEvidenceRecord.model_validate_json(terminal_json)
        except TypeError, ValueError:
            committed = False
        else:
            proposed_checkpoint = self._prepared_proposal_checkpoint(
                proposed,
                terminal,
            )
            committed = bool(
                terminal.model_dump_json() == terminal_json
                and terminal.kind is EvidenceKind.PID_SP_FIT_DECISION
                and proposed_checkpoint is not None
                and isinstance(terminal.payload, PidSpFitDecisionEvidence)
                and terminal.payload.outcome
                in {
                    PidSpLearningOutcome.REJECTED.value,
                    PidSpLearningOutcome.ACCEPTED_NEXT_COOK.value,
                }
            )
            contains = getattr(
                self._model_persistence,
                "contains_evidence",
                None,
            )
            if committed and callable(contains):
                try:
                    committed = bool(contains(terminal))
                except Exception:
                    committed = False
            else:
                committed = False
        restored = (
            self.restore_model(proposed_checkpoint)
            if committed and proposed_checkpoint is not None
            else self._abort_prepared_checkpoint(snapshot)
        )
        if not restored and committed:
            restored = self._abort_prepared_checkpoint(snapshot)
        self._persistence_revision = max(
            self._persistence_revision,
            revision,
        )
        return restored

    def _restore_decoded_checkpoint(self, checkpoint: PidSpCheckpoint) -> bool:
        current_configuration_digest = canonical_trajectory_digest(self._learning_configuration)
        immediately_authorized = (
            checkpoint.installation_identity_digest is not None
            and checkpoint.installation_identity_digest == self._installation_identity_digest
            and checkpoint.selected.configuration_digest == current_configuration_digest
        )
        if immediately_authorized:
            self._activate_selected(
                checkpoint.selected,
                revision=checkpoint.revision,
                provenance=checkpoint.provenance,
            )
            self._restore_revalidation_candidate = None
            return True
        self._restore_revalidation_candidate = checkpoint
        self._persistence_revision = max(
            self._persistence_revision,
            checkpoint.revision,
        )
        return False

    def restore_model(self, snapshot):
        try:
            if isinstance(snapshot, dict) and snapshot.get("schema") == _LEARNING_PREPARED_CHECKPOINT_SCHEMA:
                return self._restore_prepared_checkpoint(snapshot)
            if isinstance(snapshot, dict) and snapshot.get("schema") in {
                _LEARNING_CHECKPOINT_SCHEMA,
                _LEGACY_LEARNING_CHECKPOINT_SCHEMA,
            }:
                if snapshot["schema"] == _LEARNING_CHECKPOINT_SCHEMA:
                    expected_fields = {
                        "schema",
                        "revision",
                        "confirmation",
                        "identity",
                        "incumbent",
                        "installation_identity_digest",
                    }
                    pending_installation_digest = snapshot["installation_identity_digest"]
                    if pending_installation_digest is not None and (
                        not isinstance(pending_installation_digest, str)
                        or len(pending_installation_digest) != 64
                        or any(character not in "0123456789abcdef" for character in pending_installation_digest)
                    ):
                        raise ValueError("PID-SP learning installation identity is invalid")
                else:
                    expected_fields = {
                        "schema",
                        "revision",
                        "confirmation",
                        "identity",
                        "incumbent",
                    }
                    pending_installation_digest = None
                if set(snapshot) != expected_fields:
                    raise ValueError("PID-SP learning checkpoint fields are invalid")
                checkpoint_revision = snapshot["revision"]
                if type(checkpoint_revision) is not int or checkpoint_revision < 0:
                    raise ValueError("PID-SP learning revision is invalid")
                identity_value = snapshot["identity"]
                if not isinstance(identity_value, dict) or set(identity_value) != {
                    "fit_corpus_digest",
                    "configuration_digest",
                    "incumbent_digest",
                }:
                    raise ValueError("PID-SP learning identity fields are invalid")
                identity = tuple(
                    identity_value[name]
                    for name in (
                        "fit_corpus_digest",
                        "configuration_digest",
                        "incumbent_digest",
                    )
                )
                if any(
                    not isinstance(value, str)
                    or len(value) != 64
                    or any(character not in "0123456789abcdef" for character in value)
                    for value in identity
                ):
                    raise ValueError("PID-SP learning identity digests are invalid")
                configuration_current = identity[1] == canonical_trajectory_digest(self._learning_configuration)
                confirmation = decode_model_confirmation(snapshot["confirmation"])
                incumbent_value = snapshot["incumbent"]
                incumbent = None if incumbent_value is None else decode_pid_sp_checkpoint(incumbent_value)
                expected_incumbent = (
                    canonical_pid_sp_observation_model_digest(None)
                    if incumbent is None
                    else incumbent.selected.model_digest
                )
                if identity[2] != expected_incumbent:
                    raise ValueError("PID-SP learning incumbent identity is stale")
                incumbent_authorized = incumbent is None
                if incumbent is not None:
                    incumbent_authorized = self._restore_decoded_checkpoint(incumbent)
                self._persistence_revision = checkpoint_revision
                confirmation_authorized = (
                    configuration_current
                    and incumbent_authorized
                    and pending_installation_digest is not None
                    and pending_installation_digest == self._installation_identity_digest
                )
                if confirmation_authorized:
                    self._model_confirmation = confirmation
                    self._durable_confirmation_identity = identity
                else:
                    self._model_confirmation.reset()
                    self._durable_confirmation_identity = None
                self._model_comparison = None
                return True
            checkpoint = decode_pid_sp_checkpoint(snapshot)
            self._restore_decoded_checkpoint(checkpoint)
            self._model_confirmation.reset()
            self._durable_confirmation_identity = None
        except KeyError, TypeError, ValueError:
            return False
        return True

    def _seed_integral_from_identified_hold(self, error):
        """Put the integral where the identified operating point says it belongs.

        Only inside the stable window, and only once. Outside it the reset above
        clears the accumulator on every tick, so a seed placed there would be
        wiped by the same update that set it; and an operating point is a
        statement about holding, which is not what the loop is doing on the way
        up. After the first seed the integral is the loop's own, and overwriting
        it would discard the correction it exists to make.
        """
        if self._integral_seeded or self.ki == 0 or abs(error) > self.stable_window:
            return
        held = self._held_duty()
        if held is None:
            return
        self.feed_forward = held
        # Against whatever the proportional term actually sits at: when the bias
        # is already the identified duty there is no gap left to seed.
        self.inter = (held - self._bias()) / self.ki
        self._integral_seeded = True

    def _bias(self):
        """The output the proportional term sits at when the error is zero.

        The heuristic `center` reads 0.5 everywhere, where 450 F holds at 0.205
        and 225 F nearer 0.07, so the proportional term asks for far more heat
        than the chamber needs and the integral spends the approach taking it
        back. The identified model names that duty outright.
        """
        if not self.bias_from_model:
            return self.center
        held = self._held_duty()
        return self.center if held is None else held

    def _held_duty(self):
        """The duty that holds THIS cook's set point, not the model's own."""
        return self.identifier.hold_duty(target_f=_to_f(self.set_point, self.units))

    def _publish_direct_auger_allocation(self) -> float:
        """Bound the PID command and retain the exact physical auger mapping."""
        raw_output = float(self.u)
        if not math.isfinite(raw_output):
            diagnostic_raw_output = 0.0
            final_output = 0.0
            clamp_reason = AllocationClampReason.AUGER_NONFINITE
        elif raw_output < 0.0:
            diagnostic_raw_output = raw_output
            final_output = 0.0
            clamp_reason = AllocationClampReason.AUGER_MIN
        elif raw_output > 1.0:
            diagnostic_raw_output = raw_output
            final_output = 1.0
            clamp_reason = AllocationClampReason.AUGER_MAX
        else:
            diagnostic_raw_output = raw_output
            final_output = raw_output
            clamp_reason = AllocationClampReason.NONE

        self.u = final_output
        self._trace_allocation = AllocationResult(
            normalized_combustion_load=final_output,
            auger_duty=final_output,
            fan_duty=None,
            u_max=1.0,
            fan_min_pct=0.0,
            fan_max_pct=0.0,
            fan_enabled=False,
            auger_clamp_reason=clamp_reason,
            fan_clamp_reason=AllocationClampReason.NONE,
        )
        return diagnostic_raw_output

    # ------------------------------------------------------------------ control
    def update(self, current):
        current_time = time.time()
        previous_update_time = self.last_update
        previous_temperature = self.last
        dt = self._elapsed_since_last_update(current_time)
        branch = ControllerBranch.NONE
        new_target_before = self.new_target

        measured_f = _to_f(current, self.units)
        # The identified duty that holds the operating point, once there is one.
        # `center` is where the loop sits at zero error, and it is a heuristic
        # that reads 0.225 at a 225 F set point where the grill actually holds
        # near 0.07 -- the whole gap has to be carried by the integral before the
        # loop can sit still. Seeding the integral with it once, rather than
        # substituting it into the proportional term, moves the loop to the right
        # output without softening the approach: the term the heuristic inflates
        # is also what drives the last stretch up to set point, and measuring the
        # substitution showed that cost more than the offset it removed.
        trusted = self.predictor.governing_model()
        selected = _from_f(self.predictor.temperature(measured_f, current_time), self.units)
        self._selected = selected
        # Zero on the first update, where there is no earlier reading to
        # difference against rather than a rate that happens to be zero.
        measured_rate = 0.0 if previous_temperature is None else (selected - previous_temperature) / dt

        # Seed both at startup: self.last has no measured value on the very
        # first update after construction, and start_change_temp inherits the
        # same None until this same update seeds it. Neither can fire again
        # afterward -- self.last is a real number from here on, and a later
        # setpoint change copies that real value into start_change_temp
        # rather than None.
        if self.last is None:
            self.last = selected
            # There is no earlier reading to difference against on this tick, so
            # the derivative is taken against this one and is exactly zero.
            previous_temperature = selected
        # In the untrusted regime, where selected is the measured value, a
        # reading of exactly 0.0 in native units is not a temperature to
        # differentiate against; repair it the same way on a setpoint change.
        # Once a model is trusted, selected includes the predictor's
        # correction, so a faulted reading no longer reaches self.last as an
        # exact zero -- this repair's reach ends there.
        if self.last == 0.0 and self.new_target:
            self.last = selected
            previous_temperature = selected
            branch = ControllerBranch.INITIALIZATION
        if self.start_change_temp is None:
            self.start_change_temp = selected

        error = selected - self.set_point

        if error < -self.pb:
            self.u = 1.0
            if branch is ControllerBranch.NONE:
                branch = ControllerBranch.FULL_HEAT
        elif error > self.stable_window:
            self.u = 0.0
            if branch is ControllerBranch.NONE:
                branch = ControllerBranch.OVERSHOOT
        else:
            # Reset integral term when the temperature first reaches or exceeds
            # set point after a set point change.
            #
            # Reaching the set point is a crossing, not a band. A band can be
            # stepped over: a chamber whose closest approach is 4.8 F never
            # clears this, so `new_target` latches for the rest of the cook, the
            # reset below then fires on every tick, and the integral is wiped
            # before it can accumulate. Without integral action the loop parks
            # at a standing offset, which is itself far enough out to keep the
            # band unreached -- the state sustains itself, and two cooks
            # differing only in starting temperature settle 8 F apart.
            if self.new_target and (error >= 0.0 or abs(error) <= 3):
                self.new_target = False
                if branch is ControllerBranch.NONE:
                    branch = ControllerBranch.TARGET_REACHED

            # Reset integral if the system is not within the stable window, or
            # has not reached halfway to the set point within 3 cycles. Prevents
            # overshoots on small set point changes.
            reset_integral = (abs(error) > self.stable_window) or (
                self.new_target
                and current_time - self.last_set_time >= self.cycle_time * 3
                and abs(error) <= abs(self.start_change_temp - self.set_point) / 2
            )
            if reset_integral:
                self.inter = 0.0
                if branch is ControllerBranch.NONE:
                    branch = ControllerBranch.RESET

            # Minimize derivative to maximize descent rate when setting a new
            # lower set point
            if (self.new_target and self.set_point < current) or (abs(error) > self.pb / 2):
                self.derv = 0.0

            # P
            self.p = self.kp * error + self._bias()

            # I
            self.inter += error * dt
            self.i = self.ki * self.inter
            unclamped_integral_term = self.i
            self.i = max(min(self.i, self.center), -self.center)
            integral_clamped = self.i != unclamped_integral_term

            # D
            self.derv = (selected - self.last) / dt
            self.d = self.kd * self.derv

            # PID
            self.u = self.p + self.i + self.d

            # Ease off for the first three cycles after a set point change, so a
            # small change does not overshoot.
            if error < self.pb and current_time - self.last_set_time < self.cycle_time * 3:
                self.u = self.u * STARTUP_REDUCTION
        if error < -self.pb or error > self.stable_window:
            # The saturating branches never ran the integral, so no clamp of it
            # was reached on this tick whatever the previous one left behind.
            integral_clamped = False

        self._seed_integral_from_identified_hold(error)
        raw_output = self._publish_direct_auger_allocation()

        self.error = error
        self.last = selected
        self.last_update = current_time
        self._trace_diagnostics = PidSpTraceDiagnostics(
            observed_dt_seconds=dt,
            error=error,
            proportional_term=self.p,
            integral_term=self.i,
            derivative_term=self.d,
            integral_accumulator=self.inter,
            integral_clamped=integral_clamped,
            derivative_input=selected - previous_temperature,
            derivative_state=self.derv,
            proportional_band=self.pb,
            kp=self.kp,
            ki=self.ki,
            kd=self.kd,
            center=self.center,
            previous_temperature=previous_temperature,
            previous_update_time=previous_update_time,
            raw_output=raw_output,
            final_output=self.u,
            # This controller no longer extrapolates a future temperature from a
            # configured tau/theta: the Smith predictor removes the identified
            # dead time from the reading instead, so the temperature it selects
            # IS the prediction and the error taken from it IS the predicted
            # error. tau/theta come from the identified model, and read zero
            # while nothing is trusted and the selected value is the measured one.
            measured_rate=measured_rate,
            predicted_temperature=selected,
            predicted_error=error,
            # An integrating model has no time constant to report; the trace
            # field predates that form and stays zero for it.
            tau_seconds=(0.0 if trusted is None else float(trusted.get("tau", 0.0))),
            theta_seconds=(0.0 if trusted is None else trusted["theta"]),
            stable_window_seconds=self.stable_window,
            center_factor=self.center_factor,
            new_target_before=new_target_before,
            new_target_after=self.new_target,
            target_change_temperature=self.start_change_temp,
            target_change_time=self.last_set_time,
            branch=branch,
        )
        return self.u

    def trace_diagnostics(self) -> PidSpTraceDiagnostics | None:
        return self._trace_diagnostics

    def trace_allocation(self) -> AllocationResult | None:
        return self._trace_allocation

    def set_target(self, set_point):
        self.set_point = set_point
        self.error = 0.0
        self.inter = 0.0
        self._integral_seeded = False
        self.derv = 0.0
        self.last_update = time.time()
        self.last_set_time = self.last_update
        self.start_change_temp = self.last
        self.new_target = True
        # Higher centers are needed to reach higher temps, lower centers keep
        # low set points stable.
        if self.units == "F":
            if set_point <= 240:
                self.center = set_point * self.center_factor
            else:
                self.center = set_point * self.center_factor * 1.2
        elif self.units == "C":
            if set_point <= 115:
                self.center = (set_point * 9 / 5 + 32) * self.center_factor
            else:
                self.center = (set_point * 9 / 5 + 32) * self.center_factor * 1.2
        if not self._integral_seeded:
            # Until the identifier names the operating point, the heuristic
            # centre is the best estimate of it available.
            self.feed_forward = self.center
