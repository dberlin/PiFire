# Persistence Import Migration Matrix

Generated from every current Python AST import form before the persistence split: direct symbol imports, module aliases, qualified attributes, and string monkeypatch targets. Task 10 must refresh this inventory with LSP references and the same AST scan, assign any newly added importer, and finish with zero imports from `common.datastore_accessors`. Each row is one current importer/destination assignment; files that consume multiple domains intentionally appear in multiple sections.

## `common.persistence.control`

| Importer | Symbols |
|---|---|
| `blueprints/api/routes.py` | `read_control`, `write_control` |
| `blueprints/api_admin/routes.py` | `flush_control`, `read_control`, `write_control` |
| `blueprints/api_files/routes.py` | `read_control`, `write_control` |
| `blueprints/api_tuner/routes.py` | `read_control`, `write_control` |
| `blueprints/api_update/routes.py` | `read_control` |
| `blueprints/api_wizard/routes.py` | `read_control` |
| `blueprints/mobile/socket_io.py` | `flush_control`, `read_control`, `write_control` |
| `common/api_commands.py` | `mpc_calibration_command_revision`, `queue_mpc_calibration_command`, `read_control`, `write_control` |
| `common/app.py` | `write_control` |
| `common/pellets_actions.py` | `write_control` |
| `common/process_mon.py` | `read_control`, `write_control` |
| `common/system.py` | `write_control` |
| `control.py` | `flush_control` |
| `controller/model_learning/report.py` | `mpc_calibration_command_revision` |
| `controller/runtime/devices.py` | `read_control`, `write_control` |
| `controller/runtime/store.py` | `execute_control_writes`, `flush_control`, `read_control`, `write_control` |
| `display/_base_fixed.py` | `read_control`, `write_control` |
| `display/_base_flex.py` | `read_control`, `write_control` |
| `display/qtquick_flex.py` | `read_control`, `write_control` |
| `display/ssd1306b.py` | `read_control`, `write_control` |
| `notify/notifications.py` | `read_control`, `write_control` |
| `tests/characterization/test_all_writers_strict.py` | `execute_control_writes`, `read_control`, `write_control` |
| `tests/characterization/test_control_delta_seam.py` | `execute_control_writes`, `read_control`, `write_control` |
| `tests/characterization/test_control_writes_cross_writer.py` | `execute_control_writes`, `read_control`, `write_control` |
| `tests/characterization/test_process_command_golden.py` | `execute_control_writes`, `read_control`, `write_control` |
| `tests/oracle/capture_oracle.py` | `execute_control_writes`, `read_control`, `write_control` |
| `tests/ui/test_qtquick_dispatch_persistence.py` | `execute_control_writes`, `read_control`, `write_control` |
| `tests/unit/common/test_common_blobs.py` | `execute_control_writes`, `flush_control`, `read_control`, `write_control` |
| `tests/unit/common/test_import_smoke.py` | `read_control`, `write_control` |
| `tests/unit/common/test_mpc_calibration_commands.py` | `execute_control_writes`, `mpc_calibration_command_state`, `queue_mpc_calibration_command`, `read_control`, `read_pending_control_writes` |
| `tests/unit/common/test_write_kind.py` | `write_control` |
| `tests/unit/runtime/test_devices.py` | `read_control` |
| `tests/unit/wizard/test_wizard_probe_rename.py` | `execute_control_writes`, `read_control`, `write_control` |
| `tests/web/conftest.py` | `execute_control_writes`, `read_control`, `write_control` |
| `tests/web/test_api_mpc_calibration.py` | `execute_control_writes`, `read_control`, `write_control` |
| `tests/web/test_api_probe_map.py` | `execute_control_writes`, `read_control`, `write_control` |
| `tests/web/test_api_settings_update.py` | `execute_control_writes`, `read_control`, `write_control` |
| `tests/web/test_api_tuner.py` | `execute_control_writes`, `read_control`, `write_control` |
| `tests/web/test_api_tuner_auto.py` | `execute_control_writes`, `read_control`, `write_control` |
| `tests/web/test_api_update.py` | `read_control`, `write_control` |
| `tests/web/test_api_wizard.py` | `read_control`, `write_control` |
| `tests/web/test_control_liveness_not_sticky.py` | `write_control` |
| `tests/web/test_socketio_app_data.py` | `execute_control_writes`, `read_control`, `write_control` |
| `tests/web/test_webapp_sqlite.py` | `write_control` |
| `wizard.py` | `read_control`, `write_control` |

## `common.persistence.runtime`

| Importer | Symbols |
|---|---|
| `app.py` | `flush_errors`, `read_settings` |
| `blueprints/api/routes.py` | `clear_warnings_through`, `read_current`, `read_pellet_db`, `read_probe_status`, `read_settings`, `read_status`, `write_settings` |
| `blueprints/api_admin/routes.py` | `read_pellet_db`, `read_settings`, `write_pellet_db`, `write_settings` |
| `blueprints/api_history/routes.py` | `read_settings` |
| `blueprints/api_metrics/routes.py` | `read_settings` |
| `blueprints/api_tuner/routes.py` | `read_current_snapshot`, `read_settings`, `read_tr`, `write_settings` |
| `blueprints/api_update/routes.py` | `read_settings` |
| `blueprints/api_wizard/routes.py` | `read_settings`, `write_settings` |
| `blueprints/mobile/socket_io.py` | `CONTROL_HEARTBEAT_STALE_AFTER`, `flush_connected_users`, `read_connected_users`, `read_control_heartbeat`, `read_current`, `read_errors`, `read_generic_key`, `read_pellets_store`, `read_settings_store`, `read_status`, `read_warnings_snapshot`, `remove_connected_user`, `seed_pellets_store`, `seed_settings_store`, `write_connected_user`, `write_pellet_db` |
| `blueprints/wizard/wizard.py` | `read_settings` |
| `board-config.py` | `read_settings` |
| `common/api_commands.py` | `read_current`, `read_current_snapshot`, `read_pellet_db`, `read_settings`, `read_status`, `write_settings` |
| `common/app.py` | `read_settings`, `write_settings` |
| `common/backups.py` | `read_pellet_db`, `read_settings`, `write_pellet_db`, `write_warning` |
| `common/controller_model_state.py` | `read_generic_key`, `write_generic_key` |
| `common/datastore.py` | `read_settings` |
| `common/defaults.py` | `read_settings` |
| `common/pellets_actions.py` | `write_pellet_db` |
| `common/settings_migration.py` | `write_settings_store`, `write_warning` |
| `common/system.py` | `read_settings` |
| `control.py` | `flush_errors`, `read_settings` |
| `controller/model_learning/report.py` | `read_status` |
| `controller/pid_sp_learning.py` | `read_status` |
| `controller/runtime/devices.py` | `read_pellet_db`, `write_errors`, `write_generic_key`, `write_pellet_db` |
| `controller/runtime/heartbeat.py` | `CONTROL_HEARTBEAT_KEY` |
| `controller/runtime/runner.py` | `read_errors`, `write_errors` |
| `controller/runtime/store.py` | `flush_current`, `flush_errors`, `init_status`, `read_current`, `read_current_snapshot`, `read_errors`, `read_generic_key`, `read_pellet_db`, `read_settings`, `read_status`, `write_controller_model_checkpoint`, `write_current`, `write_errors`, `write_generic_key`, `write_pellet_db`, `write_status` |
| `display/_base_flex.py` | `read_current`, `read_settings`, `read_status`, `write_settings` |
| `display/qtapp.py` | `read_current`, `read_settings_store`, `read_status` |
| `display/qtquick_flex.py` | `read_status` |
| `display_launch.py` | `read_settings` |
| `display_process.py` | `flush_errors`, `read_settings` |
| `file_mgmt/cookfile.py` | `read_settings` |
| `file_mgmt/recipes.py` | `read_settings` |
| `notify/notifications.py` | `read_pellet_db`, `read_settings`, `write_settings` |
| `tests/characterization/test_all_writers_strict.py` | `init_status`, `read_settings`, `write_pellet_db`, `write_settings_store` |
| `tests/characterization/test_control_delta_seam.py` | `default_control`, `write_settings_store` |
| `tests/characterization/test_control_writes_cross_writer.py` | `default_control`, `read_settings`, `write_settings_store` |
| `tests/characterization/test_process_command_golden.py` | `flush_current`, `init_status`, `read_current`, `read_settings`, `read_status`, `write_settings`, `write_settings_store`, `write_status` |
| `tests/e2e/test_work_cycle_e2e.py` | `read_settings` |
| `tests/ui/test_qtquick_dispatch_persistence.py` | `init_status`, `read_settings`, `read_status`, `write_settings_store`, `write_status` |
| `tests/unit/bootstrap/test_startup_migration.py` | `read_pellet_db`, `read_settings`, `write_pellets_store`, `write_settings_store` |
| `tests/unit/common/test_common_blobs.py` | `clear_warnings_through`, `flush_connected_users`, `flush_errors`, `read_connected_users`, `read_errors`, `read_generic_key`, `read_probe_status`, `read_status`, `read_warnings_snapshot`, `remove_connected_user`, `write_connected_user`, `write_errors`, `write_generic_key`, `write_status`, `write_warning` |
| `tests/unit/common/test_import_smoke.py` | `read_probe_status`, `read_settings` |
| `tests/unit/common/test_install_status.py` | `datastore` |
| `tests/unit/common/test_json_blob_helpers.py` | `_read_json_blob`, `_write_json_blob`, `datastore` |
| `tests/unit/common/test_mpc_calibration_commands.py` | `apply_control_delta` |
| `tests/unit/common/test_pellets_migration_v2.py` | `read_pellets_store`, `write_pellets_store` |
| `tests/unit/common/test_pellets_schema.py` | `read_pellets_store`, `write_pellet_db` |
| `tests/unit/common/test_pellets_writers_v2.py` | `read_pellets_store`, `write_pellets_store` |
| `tests/unit/common/test_settings_migration.py` | `read_settings`, `write_settings_store` |
| `tests/unit/common/test_settings_migration_matrix.py` | `read_settings_store`, `write_settings`, `write_settings_store` |
| `tests/unit/common/test_settings_migration_retired_controllers.py` | `read_settings_store`, `write_settings_store` |
| `tests/unit/common/test_settings_schema.py` | `write_settings_store` |
| `tests/unit/common/test_write_settings_strict.py` | `read_settings`, `write_settings`, `write_settings_store` |
| `tests/unit/controller/test_heartbeat.py` | `CONTROL_HEARTBEAT_KEY`, `CONTROL_HEARTBEAT_STALE_AFTER` |
| `tests/unit/controller/test_pid_sp_learning.py` | `read_status` |
| `tests/unit/datastore/test_control_trace_store.py` | `CONTROL_TRACE_MAX_LIMIT` |
| `tests/unit/datastore/test_current_accessors.py` | `flush_current`, `read_current`, `read_current_snapshot`, `read_settings`, `write_current`, `write_settings` |
| `tests/unit/datastore/test_pellets_shape_migration.py` | `read_pellets_store`, `write_pellets_store` |
| `tests/unit/datastore/test_read_path_validation.py` | `read_settings`, `write_settings_store` |
| `tests/unit/datastore/test_settings_shape_migration.py` | `read_settings_store`, `write_settings`, `write_settings_store` |
| `tests/unit/datastore/test_settings_store_migration.py` | `read_settings`, `read_settings_store`, `write_settings`, `write_settings_store` |
| `tests/unit/datastore/test_sqlite_store_parity.py` | `time`, `write_settings` |
| `tests/unit/runtime/_persistence_helpers.py` | `ModelActivationState` |
| `tests/unit/runtime/test_devices.py` | `read_errors`, `read_pellet_db`, `write_errors` |
| `tests/unit/wizard/test_platform_pin_types.py` | `read_settings`, `write_settings_store` |
| `tests/unit/wizard/test_wizard_legacy_settings.py` | `read_settings`, `write_settings_store` |
| `tests/unit/wizard/test_wizard_probe_rename.py` | `read_settings`, `write_settings_store` |
| `tests/unit/wizard/test_wizard_run_no_probes.py` | `write_settings_store` |
| `tests/web/archive_builders.py` | `read_settings` |
| `tests/web/conftest.py` | `init_status`, `read_settings`, `write_pellets_store`, `write_settings_store` |
| `tests/web/test_api_admin_backups.py` | `read_pellet_db`, `read_pellets_store`, `read_settings` |
| `tests/web/test_api_admin_maintenance.py` | `read_pellet_db`, `read_settings`, `write_pellet_db` |
| `tests/web/test_api_admin_system.py` | `read_pellet_db`, `read_settings`, `write_pellet_db`, `write_settings` |
| `tests/web/test_api_dismiss_warnings.py` | `read_warnings_snapshot`, `write_warning` |
| `tests/web/test_api_history.py` | `read_settings` |
| `tests/web/test_api_metrics.py` | `read_settings`, `write_settings` |
| `tests/web/test_api_model_evidence.py` | `read_settings`, `read_status`, `write_settings`, `write_status` |
| `tests/web/test_api_mpc_calibration.py` | `read_settings`, `write_settings` |
| `tests/web/test_api_pellets.py` | `read_pellets_store` |
| `tests/web/test_api_pid_sp_learning.py` | `write_generic_key` |
| `tests/web/test_api_probe_map.py` | `read_settings`, `write_settings_store` |
| `tests/web/test_api_settings_controller_gate.py` | `read_settings`, `write_settings` |
| `tests/web/test_api_settings_update.py` | `read_settings`, `write_settings` |
| `tests/web/test_api_tuner.py` | `read_settings` |
| `tests/web/test_api_tuner_auto.py` | `write_current` |
| `tests/web/test_api_update.py` | `read_settings`, `write_settings_store` |
| `tests/web/test_api_wizard.py` | `read_settings`, `write_settings_store` |
| `tests/web/test_control_liveness_not_sticky.py` | `CONTROL_HEARTBEAT_KEY`, `CONTROL_HEARTBEAT_STALE_AFTER`, `default_control`, `init_status`, `read_errors`, `read_pellets_store`, `read_settings_store`, `write_errors`, `write_generic_key`, `write_pellet_db`, `write_settings_store` |
| `tests/web/test_socket_dash_payload_fields.py` | `flush_current`, `init_status`, `read_pellet_db`, `read_settings`, `read_status`, `write_generic_key`, `write_settings`, `write_status` |
| `tests/web/test_socket_probe_staleness.py` | `flush_current`, `init_status`, `read_current`, `read_pellet_db`, `read_settings`, `write_current`, `write_generic_key` |
| `tests/web/test_socket_ui_hash.py` | `flush_current`, `init_status`, `read_pellet_db`, `read_settings`, `read_status`, `write_generic_key`, `write_status` |
| `tests/web/test_socket_warnings_payload.py` | `flush_current`, `init_status`, `read_pellet_db`, `read_settings`, `write_generic_key`, `write_warning` |
| `tests/web/test_socketio_app_data.py` | `CONTROL_HEARTBEAT_KEY`, `CONTROL_HEARTBEAT_STALE_AFTER`, `flush_current`, `init_status`, `read_connected_users`, `read_current`, `read_errors`, `read_pellets_store`, `read_settings`, `read_status`, `write_connected_user`, `write_errors`, `write_generic_key`, `write_pellet_db`, `write_settings_store` |
| `tests/web/test_webapp_sqlite.py` | `init_status`, `read_connected_users`, `read_current`, `read_settings`, `remove_connected_user`, `write_connected_user`, `write_current`, `write_generic_key`, `write_pellets_store`, `write_settings_store` |
| `tools/emc2301_tach_diag.py` | `read_settings` |
| `tools/thermoworks_list.py` | `read_settings` |
| `updater.py` | `read_settings`, `write_settings` |
| `web-react/tests/e2e/seed_history.py` | `read_settings` |
| `wizard.py` | `read_settings`, `write_settings` |

## `common.persistence.history`

| Importer | Symbols |
|---|---|
| `blueprints/api_admin/routes.py` | `flush_history` |
| `blueprints/api_metrics/routes.py` | `read_all_metrics` |
| `blueprints/api_tuner/routes.py` | `autotune_length`, `flush_autotune`, `read_autotune`, `write_autotune` |
| `blueprints/mobile/socket_io.py` | `flush_history` |
| `common/app.py` | `read_all_metrics`, `read_history` |
| `control.py` | `flush_history`, `flush_metrics` |
| `controller/runtime/store.py` | `append_metric`, `flush_history`, `flush_metrics`, `read_all_metrics`, `read_history`, `read_metrics`, `update_metrics`, `write_history`, `write_tr` |
| `file_mgmt/cookfile.py` | `flush_history`, `read_all_metrics`, `read_history` |
| `notify/notifications.py` | `read_history` |
| `tests/oracle/capture_oracle.py` | `append_metric`, `read_all_metrics`, `read_history`, `read_metrics`, `update_metrics`, `write_history` |
| `tests/unit/common/test_common_blobs.py` | `autotune_length`, `flush_autotune`, `read_autotune`, `read_history`, `write_autotune`, `write_history` |
| `tests/unit/common/test_common_history.py` | `read_history`, `write_history` |
| `tests/unit/common/test_common_metrics.py` | `append_metric`, `read_all_metrics`, `read_metrics`, `update_metrics` |
| `tests/unit/datastore/test_datastore.py` | `read_history` |
| `tests/unit/file_mgmt/test_cookfile.py` | `append_metric`, `read_all_metrics`, `read_history`, `update_metrics`, `write_history` |
| `tests/web/test_api_metrics.py` | `append_metric`, `flush_metrics`, `update_metrics` |
| `tests/web/test_api_tuner.py` | `write_tr` |
| `tests/web/test_api_tuner_auto.py` | `flush_autotune`, `read_autotune`, `write_autotune`, `write_tr` |
| `tests/web/test_webapp_sqlite.py` | `read_history`, `write_history` |

## `common.persistence.control_trace`

| Importer | Symbols |
|---|---|
| `controller/control_trace_replay.py` | `read_control_trace_session` |
| `controller/mpc.py` | `append_control_trace` |
| `controller/runtime/control_trace_recorder.py` | `append_control_trace`, `prune_control_trace`, `prune_incompatible_control_trace` |
| `controller/update_mpc.py` | `read_control_trace_cook`, `read_control_trace_session` |
| `tests/unit/common/test_model_evidence_store.py` | `append_control_trace`, `prune_control_trace` |
| `tests/unit/datastore/test_control_trace_store.py` | `append_control_trace`, `delete_control_trace_session`, `prune_control_trace`, `prune_incompatible_control_trace`, `read_control_trace_cook`, `read_control_trace_range`, `read_control_trace_session` |
| `tests/unit/mpc/test_model_evidence_report.py` | `read_control_trace_session` |
| `tests/unit/mpc/test_mpc_calibration.py` | `append_control_trace` |
| `tests/unit/mpc/test_update_mpc.py` | `append_control_trace` |
| `tests/unit/runtime/test_hold_control_trace.py` | `read_control_trace_session` |

## `common.persistence.model_evidence`

| Importer | Symbols |
|---|---|
| `blueprints/api/routes.py` | `commit_model_rollback`, `read_model_activation`, `read_model_evidence` |
| `controller/model_learning/report.py` | `read_model_activation`, `read_model_evidence` |
| `controller/runtime/model_persistence.py` | `append_model_evidence`, `commit_model_activation`, `commit_model_activation_phase` |
| `controller/runtime/modes/hold.py` | `read_model_activation`, `read_model_evidence` |
| `tests/unit/common/test_model_evidence_store.py` | `append_model_evidence`, `commit_model_activation`, `invalidate_model_evidence_schema`, `read_model_activation`, `read_model_evidence`, `reset_model_evidence` |
| `tests/unit/mpc/test_grey_learning_snapshot_migration.py` | `read_model_activation`, `read_model_evidence` |
| `tests/unit/mpc/test_model_evidence_report.py` | `read_model_evidence` |
| `tests/unit/runtime/test_hold_model_persistence.py` | `append_model_evidence`, `commit_model_activation_phase`, `commit_model_rollback`, `read_model_activation`, `read_model_evidence` |
| `tests/unit/runtime/test_model_persistence.py` | `append_model_evidence`, `commit_model_activation_phase`, `read_model_activation` |
| `tests/web/test_api_model_evidence.py` | `append_model_evidence`, `commit_model_activation_phase`, `read_model_activation`, `read_model_evidence` |

## `common.persistence.install_state`

| Importer | Symbols |
|---|---|
| `blueprints/api_update/routes.py` | `get_updater_install_status`, `set_updater_install_status` |
| `blueprints/api_wizard/routes.py` | `delete_wizard_install_info`, `get_wizard_install_status`, `load_wizard_install_info`, `set_wizard_install_status`, `store_wizard_install_info` |
| `common/system.py` | `load_os_info`, `store_os_info` |
| `tests/unit/common/test_common_blobs.py` | `get_wizard_install_status`, `set_wizard_install_status` |
| `tests/unit/common/test_install_status.py` | `get_updater_install_status`, `get_wizard_install_status`, `set_updater_install_status`, `set_wizard_install_status` |
| `tests/unit/common/test_os_info_read_path_is_pure.py` | `load_os_info`, `store_os_info` |
| `tests/unit/updater/test_acados_build.py` | `get_updater_install_status` |
| `tests/unit/wizard/test_platform_pin_types.py` | `get_wizard_install_status` |
| `tests/unit/wizard/test_wizard_run_no_probes.py` | `get_wizard_install_status` |
| `tests/web/test_api_wizard.py` | `load_wizard_install_info`, `store_wizard_install_info` |
| `updater.py` | `set_updater_install_status`, `set_wizard_install_status` |
| `wizard.py` | `load_wizard_install_info`, `set_updater_install_status`, `set_wizard_install_status` |

## `controller.model_learning.migration`

| Importer | Symbols |
|---|---|
| `controller/runtime/modes/hold.py` | `migrate_mpc_learning_authority` |
| `tests/unit/mpc/test_grey_learning_snapshot_migration.py` | `migrate_mpc_learning_authority` |

## Cutover Order

1. Control and runtime blobs; run characterization control tests, runtime store/device tests, current/settings/pellet/status tests, SQLite parity, and the real-store work-cycle E2E.
2. History/metrics/autotune and install state; run common history/metrics/blob/install tests, file management, tuner/metrics, updater, wizard, and OS-info tests.
3. Control trace; run control-trace recorder/replay/update/MPC/Hold trace tests.
4. Model evidence and controller migration policy; run model persistence/report/activation/migration and model-evidence web tests.
5. Import smoke and deletion; remove `common/datastore_accessors.py`, run LSP workspace references plus all-form AST import search, then run the aggregate persistence/runtime/web suites.

Each numbered cutover is a sequential Jujutsu change. Do not run these caller migrations concurrently because several importers consume more than one destination domain.
