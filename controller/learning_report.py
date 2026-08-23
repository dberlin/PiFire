"""Lazy dispatch for controller-specific final learning reports."""

from importlib import import_module

from common.cook_diagnostics import ControllerLearningReport

_PROVIDER_MODULES = {
    "mpc": "controller.model_learning.report",
    "pid_sp": "controller.pid_sp_learning",
}
_PROVIDER_FUNCTION = "diagnostic_learning_report"


def controller_learning_report(controller_name: str) -> ControllerLearningReport | None:
    """Return an owned report from the selected controller provider."""

    provider_module = _PROVIDER_MODULES.get(controller_name)
    if provider_module is None:
        return None

    provider = getattr(import_module(provider_module), _PROVIDER_FUNCTION)
    report = provider()
    if not isinstance(report, ControllerLearningReport):
        raise TypeError(f"{provider_module}.{_PROVIDER_FUNCTION} must return ControllerLearningReport")
    if report.controller != controller_name:
        raise ValueError(f"provider for {controller_name} returned a report for {report.controller}")
    return ControllerLearningReport(
        controller=report.controller,
        schema_version=report.schema_version,
        revision=report.revision,
        report=report.report,
    )


def controller_learning_report_revision(controller_name: str) -> str | None:
    """Return the selected controller's report revision, when supported."""

    report = controller_learning_report(controller_name)
    return None if report is None else report.revision
