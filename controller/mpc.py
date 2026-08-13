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
from controller.acados import SolverDiagnostics
from controller.applied_output import AppliedOutput
from controller.base import ControllerBase, MpcTraceDiagnostics
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
    from controller.mpc_calibration import CalibrationCommand, CompletedCalibrationResult


class Controller(ControllerBase):
    def __init__(
        self,
        config,
        units,
        cycle_data,
        *,
        activation_persistence: ModelPersistenceWorker | None = None,
    ):
        super().__init__(config, units, cycle_data)

        self._activation_configuration = {
            "controller": "mpc",
            "config": copy.deepcopy(config or {}),
            "cycle_data": copy.deepcopy(cycle_data),
            "units": units,
        }
        cfg = normalize_config(config)
        self.cfg = cfg
        self.u_max = float(cycle_data.get("u_max", 0.9))
        self._trace_diagnostics = None
        horizon_steps = cfg["n_horizon"]
        if isinstance(horizon_steps, bool) or not isinstance(horizon_steps, int):
            raise RuntimeError("normalized n_horizon must be an integer")
        self._calibration = MpcCalibrationRuntime(
            horizon_steps=horizon_steps,
            u_max=self.u_max,
        )
        self._trace_baseline_allocation: AllocationResult | None = None
        self._trace_allocation: AllocationResult | None = None
        self._pair_factory = MpcPairFactory(
            cfg,
            units,
            cycle_data,
            advance_calibration=self._calibration.advance,
            model_authority=self._core_model_authority,
            on_policy_failure=self._handle_core_policy_failure,
        )
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
        self._activation_runtime = ActivationRuntime(
            self._pair_factory,
            initial_pair,
            persistence,
        )
        self.cfg = self._core.config
        self.u_max = self._core.u_max
        warn_about_model(self.cfg)
        self._closed = False
        try:
            from common.persistence.control_trace import append_control_trace

            self._grey_learning_runtime = GreyLearningRuntime(
                pair_factory=self._pair_factory,
                activation_runtime=self._activation_runtime,
                learning_enabled=cfg.get("enable_online_adaptation") is True,
                units=units,
                cycle_data=copy.deepcopy(cycle_data),
                active_pair=lambda: self._activation_runtime.active_pair,
                active_components=self._active_learning_components,
                configuration=self._learning_configuration,
                snapshot_parameters=lambda: self._core.snapshot_parameters(),
                cook_history=lambda: tuple(self._core.history),
                sync_configuration=self._sync_learning_configuration,
                append_trace=append_control_trace,
            )
        except BaseException:
            self._activation_runtime.close()
            raise

    @property
    def _core(self):
        return self._activation_runtime.active_pair.core

    @property
    def estimator(self):
        return self._core.estimator

    @property
    def mpc(self):
        return self._core.solver

    @property
    def _set_point_c(self):
        return self._core.set_point_c

    @property
    def _last_combustion_load(self):
        return self._core.last_combustion_load

    @property
    def _applied_combustion_load(self):
        return self._core.applied_combustion_load

    @property
    def _x_hat(self):
        return self._core.estimate

    @property
    def _last_raw_combustion_load(self):
        return self._core.last_raw_combustion_load

    @property
    def _last_equilibrium_load(self):
        return self._core.last_equilibrium_load

    @property
    def _last_residual_load(self):
        return self._core.last_residual_load

    @property
    def _last_feasibility(self):
        return self._core.last_feasibility

    @property
    def _consecutive_policy_failures(self):
        return self._core.consecutive_policy_failures

    @property
    def _history(self):
        return self._core.history

    @property
    def _native_failure_diagnostics(self):
        return self._core.native_failure_diagnostics

    def _core_model_authority(self):
        if not hasattr(self, "_grey_learning_runtime"):
            return 0, None
        return self._grey_learning_runtime.model_authority()


    def _handle_core_policy_failure(self, _error):
        if self._activation_runtime.active_record is not None:
            if not self.activation_runtime_failure("native-solve-failure"):
                self.terminate_mpc_activation("native-failure-compensation-failed")


    def _active_learning_components(self) -> CandidatePair:
        return CandidatePair(self._core.estimator, self._core.solver)

    def _learning_configuration(self) -> MpcConfig:
        return copy.deepcopy(self._core.config)

    def _sync_learning_configuration(self) -> None:
        self.cfg = self._core.config

    def set_target(self, set_point):
        self.set_point = set_point
        self._core.set_target(set_point)
        self._calibration.set_target_c(self._core.set_point_c)

    def get_control_period(self):
        return float(self.cfg["control_period"])

    def commands_fan(self):
        return bool(self.cfg.get("enable_fan_input", False))

    def wants_async(self):
        return True

    @staticmethod
    def _normalized_forecast_failure(error):
        """Normalize model-library finite-value errors to the lifecycle reason."""
        if isinstance(error, (ValueError, FloatingPointError, RuntimeError)) and "finite" in str(error).lower():
            return ValueError("non-finite-forecast")
        return error

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
        authorized = self._activation_runtime.authorize_candidate_pair(record)
        if authorized:
            self._grey_learning_runtime.sync_activation_generation()
        return authorized

    def compensate_candidate_pair(
        self,
        pair: OwnedMpcPair,
        record: PreparedActivationRecord,
        reason: str,
    ) -> bool:
        compensated = self._activation_runtime.compensate_candidate_pair(
            pair,
            record,
            reason,
        )
        if compensated:
            self._grey_learning_runtime.sync_activation_generation()
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
        advanced = self._activation_runtime.advance_activation()
        self._grey_learning_runtime.sync_activation_generation()
        return advanced

    def terminate_mpc_activation(self, reason: str) -> None:
        self._activation_runtime.terminate(reason)

    def submit_activation_confidence(
        self,
        record: ModelEvidenceRecord,
    ) -> DurableActivationReceipt:
        return self._activation_runtime.submit_activation_confidence(record)

    def restore_activation(
        self,
        persisted: ModelActivationState,
        records: Sequence[ModelEvidenceRecord],
    ) -> bool:
        restored = self._activation_runtime.restore_activation(persisted, records)
        if restored:
            self._grey_learning_runtime.sync_activation_generation(exact=True)
        return restored

    def activation_runtime_failure(self, reason: str) -> bool:
        restored = self._activation_runtime.activation_runtime_failure(reason)
        if restored:
            self._grey_learning_runtime.sync_activation_generation()
        return restored

    def rollback_activation(self, reason: str) -> bool:
        restored = self._activation_runtime.rollback_activation(reason)
        if restored:
            self._grey_learning_runtime.sync_activation_generation()
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

    def get_status(self):
        model_meta = self._grey_learning_runtime.model_metadata
        return {
            "set_point": finite_float(getattr(self, "set_point", self._set_point_c)),
            "set_point_c": finite_float(self._set_point_c),
            "last_combustion_load": finite_float(self._last_combustion_load),
            "last_raw_combustion_load": optional_float(self._last_raw_combustion_load),
            "last_equilibrium_load": optional_float(self._last_equilibrium_load),
            "last_residual_load": optional_float(self._last_residual_load),
            "applied_combustion_load": finite_float(self._applied_combustion_load),
            "policy": "acados-grey",
            "policy_kind": "acados-grey",
            "n_horizon": int(self.cfg["n_horizon"]),
            # Non-zero means update() is returning a held command rather than a
            # computed one, so the number this reports is how many control
            # periods the grill has been running open-loop.
            "policy_failures": int(self._consecutive_policy_failures),
            "u_max": finite_float(self.u_max),
            "x_hat": None
            if self._x_hat is None
            else tuple(finite_float(v) for v in np.asarray(self._x_hat).reshape(-1)),
            # The __dict__ fallback this replaces reached the pid_cycle_data mqtt
            # topic only through notify()'s nested-dict recursion over this same
            # attribute; publish it explicitly so that topic keeps working.
            # cycle_data is core.__dict__'s live reference to settings["cycle_data"]
            # (see _build_core) -- sanitized_copy hands back a copy, not that
            # live settings mapping.
            "cycle_data": sanitized_copy(self.cycle_data),
            # None until a model has been adopted this process (fresh install,
            # or before the first fit completes): a model identified at one
            # temperature does not describe another, so the band it was fit
            # over travels with the fit error rather than being assumed global.
            "model": None
            if model_meta is None
            else {
                "band_c": [finite_float(v) for v in model_meta["band_c"]],
                "rmse": optional_float(model_meta["rmse"]),
            },
            "feasibility": None if self._last_feasibility is None else self._last_feasibility.as_status(),
            "learning": self._grey_learning_runtime.learning_status(),
            "activation": {
                "active_kind": _snapshot.GREY_BOX_KIND,
                "active_digest": self.active_control_pair.descriptor.model_digest,
                "decision_id": (
                    None
                    if self._activation_runtime.active_record is None
                    else self._activation_runtime.active_record.decision_id
                ),
                "role_generation": self.active_control_pair.descriptor.role_generation,
                "failed_digest": None,
                "failed_generation": None,
                "last_safe_command": finite_float(self._last_combustion_load),
                "fallback_kind": (
                    _snapshot.GREY_BOX_KIND
                    if self._activation_runtime.terminated_reason is not None
                    else None
                ),
                "fallback_reason": self._activation_runtime.terminated_reason,
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
        self._core.set_output(applied)
        self._calibration.register_output(applied)

    def update(self, current):
        step = self._core.update(current)
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
        return self._core.native_failure_diagnostics

    def close(self):
        """Release learning, native solver, estimator, and persistence exactly once."""
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
            raise RuntimeError("could not close complete MPC controller ownership") from errors[0]
