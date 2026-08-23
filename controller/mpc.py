#!/usr/bin/env python3

"""Grey-box MPC control through the repository-published acados runtime.

The active controller owns one EKF/KF estimator and one native solver.  The
configured control period is estimator/runner cadence; the prediction model is
the fixed 25-second, eight-delay generated map.
"""

from __future__ import annotations


from collections.abc import Sequence
import copy
import logging
from typing import TYPE_CHECKING

import numpy as np

from common.controller_model_state import ControllerModelStore
from common.model_evidence import ModelEvidenceRecord
from common.persistence.model_evidence import ModelActivationState
from controller import mpc_snapshot as _snapshot
from controller.applied_output import AppliedOutput
from controller.base import ControllerBase, ControllerLearningDiagnostics, MpcTraceDiagnostics
from controller.model_learning.activation import PreparedActivationRecord
from controller.model_learning.activation_runtime import ActivationRuntime
from controller.model_learning.calibration import CalibrationDecision
from controller.model_learning.grey_runtime import GreyLearningRuntime
from controller.mpc_allocator import AllocationResult
from controller.mpc_calibration import MpcCalibrationRuntime
from controller.mpc_config import (
    MpcConfig,
    finite_float,
    normalize_config,
    optional_float,
    sanitized_copy,
    warn_about_model,
)
from controller.mpc_factory import MpcPairFactory, OwnedMpcPair
from controller.runtime.model_fitting import CandidatePair
from controller.runtime.model_persistence import (
    DurableActivationReceipt,
    ModelPersistenceWorker,
)

if TYPE_CHECKING:
    from controller.acados import SolverDiagnostics
    from controller.mpc_calibration import CalibrationCommand, CompletedCalibrationResult


class Controller(ControllerBase):
    def __init__(
        self,
        config,
        units,
        cycle_data,
        *,
        activation_persistence: ModelPersistenceWorker | None = None,
        logger=None,
    ):
        super().__init__(config, units, cycle_data, logger=logger)

        cfg = normalize_config(config)
        self.cfg = cfg
        self.u_max = float(cycle_data.get("u_max", 0.9))
        self.set_point = 0.0
        self._trace_diagnostics: MpcTraceDiagnostics | None = None
        self._trace_baseline_allocation: AllocationResult | None = None
        self._trace_allocation: AllocationResult | None = None
        self._closed = False

        horizon_steps = cfg["n_horizon"]
        if isinstance(horizon_steps, bool) or not isinstance(horizon_steps, int):
            raise RuntimeError("normalized n_horizon must be an integer")
        self._calibration = MpcCalibrationRuntime(
            horizon_steps=horizon_steps,
            u_max=self.u_max,
        )
        grey_runtime: GreyLearningRuntime | None = None

        def model_authority():
            if grey_runtime is None:
                return 0, None
            return grey_runtime.model_authority()

        def handle_policy_failure(_error):
            if self._activation_runtime.active_record is not None:
                if not self.activation_runtime_failure("native-solve-failure"):
                    self.terminate_mpc_activation("native-failure-compensation-failed")

        self._pair_factory = MpcPairFactory(
            cfg,
            units,
            cycle_data,
            advance_calibration=self._calibration.advance,
            model_authority=model_authority,
            on_policy_failure=handle_policy_failure,
        )

        initial_pair: OwnedMpcPair | None = None
        persistence: ModelPersistenceWorker | None = None
        activation_runtime: ActivationRuntime | None = None
        try:
            initial_pair = self._pair_factory.build(
                self._pair_factory.configured(
                    cfg,
                    candidate_generation=0,
                    role_generation=0,
                ),
                authorized=True,
            )
            persistence = (
                activation_persistence
                if activation_persistence is not None
                else ModelPersistenceWorker(
                    ControllerModelStore(),
                    logging.getLogger("control"),
                )
            )
            activation_runtime = ActivationRuntime(
                self._pair_factory,
                initial_pair,
                persistence,
            )
            self._activation_runtime = activation_runtime
            self._sync_learning_configuration()
            self.u_max = self.active_control_pair.core.u_max
            warn_about_model(self.cfg, logger=self._logger)

            from common.persistence.control_trace import append_control_trace

            grey_runtime = GreyLearningRuntime(
                pair_factory=self._pair_factory,
                activation_runtime=activation_runtime,
                learning_enabled=cfg.get("enable_online_adaptation") is True,
                units=units,
                cycle_data=copy.deepcopy(cycle_data),
                active_pair=self._active_pair_for_learning,
                active_components=self._active_learning_components,
                configuration=self._learning_configuration,
                snapshot_parameters=self._snapshot_parameters_for_learning,
                cook_history=self._history_for_learning,
                sync_configuration=self._sync_learning_configuration,
                append_trace=append_control_trace,
                logger=self._logger,
            )
            self._grey_learning_runtime = grey_runtime
        except BaseException as construction_error:
            cleanup_errors: list[BaseException] = []
            if activation_runtime is not None:
                try:
                    activation_runtime.close()
                except BaseException as cleanup_error:
                    cleanup_errors.append(cleanup_error)
            else:
                if persistence is not None:
                    try:
                        persistence.flush_and_stop(timeout=0.1)
                    except BaseException as cleanup_error:
                        cleanup_errors.append(cleanup_error)
                if initial_pair is not None:
                    try:
                        initial_pair.close()
                    except BaseException as cleanup_error:
                        cleanup_errors.append(cleanup_error)
            for cleanup_error in cleanup_errors:
                construction_error.add_note(f"Controller construction cleanup failed: {cleanup_error!r}")
            raise

    @property
    def estimator(self):
        return self.active_control_pair.core.estimator

    @property
    def mpc(self):
        return self.active_control_pair.core.solver

    def _active_pair_for_learning(self) -> OwnedMpcPair:
        return self.active_control_pair

    def _active_learning_components(self) -> CandidatePair:
        core = self.active_control_pair.core
        return CandidatePair(core.estimator, core.solver)

    def _learning_configuration(self) -> MpcConfig:
        return copy.deepcopy(self.active_control_pair.core.config)

    def _snapshot_parameters_for_learning(self):
        return copy.deepcopy(self.active_control_pair.core.snapshot_parameters())

    def _history_for_learning(self):
        return tuple(self.active_control_pair.core.history)

    def _sync_learning_configuration(self) -> None:
        self.cfg = self.active_control_pair.core.config

    def _synchronize_activation_transition(self, *, exact: bool = False) -> None:
        self._sync_learning_configuration()
        if exact:
            self._grey_learning_runtime.sync_activation_generation(exact=True)
        else:
            self._grey_learning_runtime.sync_activation_generation()

    def _activation_identity_changed(
        self,
        previous_pair: OwnedMpcPair,
        previous_generation: int,
        *,
        pair_change_is_commit: bool = True,
    ) -> bool:
        runtime = self._activation_runtime
        generation_changed = runtime.role_generation != previous_generation
        pair_changed = runtime.active_pair is not previous_pair
        return generation_changed or (pair_change_is_commit and pair_changed)

    def set_target(self, set_point):
        self.set_point = set_point
        core = self.active_control_pair.core
        core.set_target(set_point)
        self._calibration.set_target_c(core.set_point_c)

    def get_control_period(self):
        return float(self.cfg["control_period"])

    def commands_fan(self):
        return bool(self.cfg.get("enable_fan_input", False))

    def wants_async(self):
        return True

    @property
    def active_control_pair(self) -> OwnedMpcPair:
        return self._activation_runtime.active_pair

    @property
    def rollback_control_pair(self) -> OwnedMpcPair | None:
        return self._activation_runtime.rollback_pair

    @property
    def activation_output_authorized(self) -> bool:
        return self._activation_runtime.output_authorized

    @property
    def failed_role_generations(self) -> frozenset[int]:
        return self._activation_runtime.failed_role_generations

    @property
    def activation_terminated(self) -> bool:
        return self._activation_runtime.activation_terminated

    def install_candidate_pair_inert(
        self,
        pair: OwnedMpcPair,
        record: PreparedActivationRecord,
    ) -> bool:
        return self._activation_runtime.install_candidate_pair_inert(pair, record)

    def authorize_candidate_pair(self, record: PreparedActivationRecord) -> bool:
        runtime = self._activation_runtime
        previous_pair = runtime.active_pair
        previous_generation = runtime.role_generation
        authorized = runtime.authorize_candidate_pair(record)
        if authorized or self._activation_identity_changed(
            previous_pair,
            previous_generation,
        ):
            self._synchronize_activation_transition()
        return authorized

    def compensate_candidate_pair(
        self,
        pair: OwnedMpcPair,
        record: PreparedActivationRecord,
        reason: str,
    ) -> bool:
        runtime = self._activation_runtime
        previous_pair = runtime.active_pair
        previous_generation = runtime.role_generation
        compensated = runtime.compensate_candidate_pair(
            pair,
            record,
            reason,
        )
        if compensated or self._activation_identity_changed(
            previous_pair,
            previous_generation,
        ):
            self._synchronize_activation_transition()
        return compensated

    def queue_prepared_activation(
        self,
        record: PreparedActivationRecord,
        candidate_pair: OwnedMpcPair,
        prepared_receipt: DurableActivationReceipt,
    ) -> bool:
        return self._activation_runtime.queue_prepared_activation(
            record,
            candidate_pair,
            prepared_receipt,
        )

    def advance_activation(self) -> bool:
        runtime = self._activation_runtime
        previous_pair = runtime.active_pair
        previous_generation = runtime.role_generation
        advanced = runtime.advance_activation()
        if self._activation_identity_changed(
            previous_pair,
            previous_generation,
            # An inert first-stage install changes the pair before generation commits.
            pair_change_is_commit=False,
        ):
            self._synchronize_activation_transition()
        return advanced

    def terminate_mpc_activation(self, reason: str) -> None:
        self._activation_runtime.terminate(reason)

    def submit_activation_confidence(
        self,
        record: ModelEvidenceRecord,
        *,
        preceding_evidence: Sequence[ModelEvidenceRecord] = (),
    ) -> DurableActivationReceipt:
        return self._activation_runtime.submit_activation_confidence(
            record,
            preceding_evidence=preceding_evidence,
        )

    def restore_activation(
        self,
        persisted: ModelActivationState,
        records: Sequence[ModelEvidenceRecord],
    ) -> bool:
        runtime = self._activation_runtime
        previous_pair = runtime.active_pair
        previous_generation = runtime.role_generation
        restored = runtime.restore_activation(persisted, records)
        if restored or self._activation_identity_changed(
            previous_pair,
            previous_generation,
        ):
            self._synchronize_activation_transition(exact=True)
        return restored

    def activation_runtime_failure(self, reason: str) -> bool:
        runtime = self._activation_runtime
        previous_pair = runtime.active_pair
        previous_generation = runtime.role_generation
        restored = runtime.activation_runtime_failure(reason)
        if restored or self._activation_identity_changed(
            previous_pair,
            previous_generation,
        ):
            self._synchronize_activation_transition()
        return restored

    def rollback_activation(self, reason: str) -> bool:
        runtime = self._activation_runtime
        previous_pair = runtime.active_pair
        previous_generation = runtime.role_generation
        restored = runtime.rollback_activation(reason)
        if restored or self._activation_identity_changed(
            previous_pair,
            previous_generation,
        ):
            self._synchronize_activation_transition()
        return restored

    def drain_activation_events(self) -> tuple[ModelEvidenceRecord, ...]:
        return self._activation_runtime.drain_activation_events()

    def observe_frame(self, observation):
        return self._grey_learning_runtime.observe_frame(observation)

    def observation_failure(self, observation, error):
        return self._grey_learning_runtime.observation_failure(observation, error)

    def bind_learning_identity(self, session_id, cook_id, role_generation):
        return self._grey_learning_runtime.bind_learning_identity(session_id, cook_id, role_generation)

    def poll_learning_off_path(self, *, live_origin=None):
        return self._grey_learning_runtime.poll_learning_off_path(live_origin=live_origin)

    def get_learning_diagnostics(self) -> ControllerLearningDiagnostics:
        return ControllerLearningDiagnostics(
            schema_version=1,
            state=self._grey_learning_runtime.learning_status(),
        )

    def _build_status(self, diagnostics):
        core = self.active_control_pair.core
        active_pair = self.active_control_pair
        model_meta = self._grey_learning_runtime.model_metadata
        estimate = core.estimate
        feasibility = core.last_feasibility
        active_record = self._activation_runtime.active_record
        terminated_reason = self._activation_runtime.terminated_reason
        return {
            "set_point": finite_float(self.set_point),
            "set_point_c": finite_float(core.set_point_c),
            "last_combustion_load": finite_float(core.last_combustion_load),
            "last_raw_combustion_load": optional_float(core.last_raw_combustion_load),
            "last_equilibrium_load": optional_float(core.last_equilibrium_load),
            "last_residual_load": optional_float(core.last_residual_load),
            "applied_combustion_load": finite_float(core.applied_combustion_load),
            "policy": "acados-grey",
            "policy_kind": "acados-grey",
            "n_horizon": int(self.cfg["n_horizon"]),
            "policy_failures": int(core.consecutive_policy_failures),
            "u_max": finite_float(self.u_max),
            "x_hat": (
                None if estimate is None else tuple(finite_float(value) for value in np.asarray(estimate).reshape(-1))
            ),
            "cycle_data": sanitized_copy(self.cycle_data),
            "model": (
                None
                if model_meta is None
                else {
                    "band_c": [finite_float(value) for value in model_meta["band_c"]],
                    "rmse": optional_float(model_meta["rmse"]),
                }
            ),
            "feasibility": (None if feasibility is None else feasibility.as_status()),
            "learning": diagnostics.as_json(),
            "activation": {
                "active_kind": _snapshot.GREY_BOX_KIND,
                "active_digest": active_pair.descriptor.model_digest,
                "decision_id": (None if active_record is None else active_record.decision_id),
                "role_generation": active_pair.descriptor.role_generation,
                "failed_digest": None,
                "failed_generation": None,
                "last_safe_command": finite_float(core.last_combustion_load),
                "fallback_kind": (_snapshot.GREY_BOX_KIND if terminated_reason is not None else None),
                "fallback_reason": terminated_reason,
            },
        }

    #: The model structure a snapshot describes, shared with every other thing
    #: that outlives the process and claims to describe this model (see
    #: mpc_model.MODEL_SCHEMA) rather than counted separately here -- two
    #: numbers meaning the same thing is how they drift.
    #:
    #: A version 1 record describes the two-lump model this controller no
    #: longer has: its C_f and h_fc name nothing, and the C_c, h_amb and K_Q
    #: beside them were fitted against a chamber that was fed through a
    #: firepot, so they are not this model's parameters under a shorter name.
    #: Applying the subset that still has matching keys would put a stranger's
    #: numbers on a live grill, so `restore_model` refuses the record and says
    #: so; the next cook refits from scratch, which is what a fresh install
    #: does anyway.

    def get_model_snapshot(self):
        return self._grey_learning_runtime.get_model_snapshot()

    def restore_model(self, snapshot):
        return self._grey_learning_runtime.restore_model(snapshot)

    def finalize_cook_refit(self, outcome) -> bool:
        return self._grey_learning_runtime.finalize_cook_refit(outcome)

    def cook_history(self):
        return self._grey_learning_runtime.cook_history()

    def refit_from_cook(self, history=None):
        return self._grey_learning_runtime.refit_from_cook(history)

    def set_safety_ceiling_c(self, ceiling_c: float) -> None:
        self._calibration.set_safety_ceiling_c(ceiling_c)

    def request_calibration(self, command: CalibrationCommand) -> None:
        self._calibration.request(command)

    def cancel_calibration(self, reason: str) -> None:
        self._calibration.cancel(reason)

    def register_calibration_result(self, result: CompletedCalibrationResult) -> None:
        self._calibration.register_result(result)

    def set_output(self, applied: AppliedOutput) -> None:
        self.active_control_pair.core.set_output(applied)
        self._calibration.register_output(applied)

    def update(self, current):
        step = self.active_control_pair.core.update(current)
        self._trace_diagnostics = step.diagnostics
        self._trace_baseline_allocation = step.baseline_allocation
        self._trace_allocation = step.allocation
        return {"cycle_ratio": step.cycle_ratio, "fan": dict(step.fan)}

    def trace_diagnostics(self) -> MpcTraceDiagnostics | None:
        return self._trace_diagnostics

    def trace_allocation(self) -> AllocationResult | None:
        return self._trace_allocation

    def trace_baseline_allocation(self) -> AllocationResult | None:
        return self._trace_baseline_allocation

    def trace_calibration(self) -> CalibrationDecision:
        return self._calibration.decision

    def native_failure_diagnostics(self) -> SolverDiagnostics | None:
        return self.active_control_pair.core.native_failure_diagnostics

    def close(self):
        """Stop learning, persistence, and native owners exactly once."""
        if self._closed:
            return
        self._closed = True
        errors: list[BaseException] = []
        try:
            self._grey_learning_runtime.close()
        except BaseException as error:
            errors.append(error)
        try:
            self._activation_runtime.close()
        except BaseException as error:
            errors.append(error)
        if errors:
            raise BaseExceptionGroup(
                "could not close complete MPC controller ownership",
                errors,
            )
