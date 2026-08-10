"""Controller-specific learning report invalidation dispatch."""


def controller_learning_report_revision(controller_name: str) -> str | None:
    """Return the selected controller's report revision, when supported."""

    if controller_name == "mpc":
        from controller.model_learning.report import learning_report_revision

        return learning_report_revision()
    if controller_name == "pid_sp":
        from controller.pid_sp_learning import backend_pid_sp_learning_report

        return backend_pid_sp_learning_report().revision
    return None
