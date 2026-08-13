# Persistence Import Migration Matrix

Generated from every current Python AST import form before the persistence split: direct symbol imports, module aliases, qualified attributes, `patch.object`/module attribute targets, and fully qualified string monkeypatch targets. The inventory was refreshed with LSP references for all 72 remaining public monolith symbols. Each row is one current importer/destination assignment; files that consume multiple domains intentionally appear in multiple sections.

## Locked Inventory

- Unique remaining monolith importers: **119**
- Importer/symbol assignments: **499**
- Destination rows: **181**
- Distinct referenced monolith symbols: **76**
- Missing assignments: **none**
- Stale assignments: **none**

`controller/runtime/store.py` is absent because the current composed runtime store no longer imports the monolith. Task 8 model-evidence and migration-policy callers are likewise already migrated, so their destination sections are intentionally empty rather than carrying stale rows.

## `common.persistence.control`

| Importer | Symbols |
|---|---|
| `blueprints/api/routes.py` | `enqueue_control_delta, read_control` |
| `blueprints/api_admin/routes.py` | `enqueue_control_delta, flush_control, read_control` |
| `blueprints/api_files/routes.py` | `enqueue_control_delta, read_control` |
| `blueprints/api_tuner/routes.py` | `enqueue_control_delta, read_control` |
| `blueprints/api_update/routes.py` | `read_control` |
| `blueprints/api_wizard/routes.py` | `read_control` |
| `blueprints/mobile/socket_io.py` | `enqueue_control_delta, flush_control, read_control` |
| `common/api_commands.py` | `enqueue_control_delta, mpc_calibration_command_revision, queue_mpc_calibration_command, read_control` |
| `common/app.py` | `enqueue_control_delta` |
| `common/pellets_actions.py` | `enqueue_control_delta` |
| `common/process_mon.py` | `read_control, write_control_snapshot` |
| `common/system.py` | `enqueue_control_delta` |
| `controller/model_learning/report.py` | `mpc_calibration_command_revision` |
| `controller/runtime/devices.py` | `read_control, write_control_snapshot` |
| `display/_base_fixed.py` | `enqueue_control_delta, read_control` |
| `display/_base_flex.py` | `enqueue_control_delta, read_control` |
| `display/qtquick_flex.py` | `enqueue_control_delta, read_control` |
| `display/ssd1306b.py` | `enqueue_control_delta, read_control` |
| `notify/notifications.py` | `read_control, write_control_snapshot` |
| `tests/characterization/test_all_writers_strict.py` | `execute_control_writes, read_control, write_control_snapshot` |
| `tests/characterization/test_control_delta_seam.py` | `default_control, enqueue_control_delta, execute_control_writes, read_control, write_control_snapshot` |
| `tests/characterization/test_control_writes_cross_writer.py` | `default_control, enqueue_control_delta, execute_control_writes, read_control, write_control_snapshot` |
| `tests/characterization/test_process_command_golden.py` | `execute_control_writes, read_control, write_control_snapshot` |
| `tests/oracle/capture_oracle.py` | `enqueue_control_delta, execute_control_writes, read_control, write_control_snapshot` |
| `tests/ui/test_qtquick_dispatch_persistence.py` | `execute_control_writes, read_control, write_control_snapshot` |
| `tests/unit/common/test_common_blobs.py` | `enqueue_control_delta, execute_control_writes, flush_control, read_control, write_control_snapshot` |
| `tests/unit/common/test_import_smoke.py` | `enqueue_control_delta, read_control, write_control_snapshot` |
| `tests/unit/common/test_mpc_calibration_commands.py` | `apply_control_delta, execute_control_writes, mpc_calibration_command_state, queue_mpc_calibration_command, read_control, read_pending_control_writes` |
| `tests/unit/common/test_write_kind.py` | `write_control_snapshot` |
| `tests/unit/persistence/test_domain_contracts.py` | `read_pending_control_writes` |
| `tests/unit/runtime/test_devices.py` | `read_control` |
| `tests/unit/wizard/test_wizard_probe_rename.py` | `enqueue_control_delta, execute_control_writes, read_control, write_control_snapshot` |
| `tests/web/conftest.py` | `execute_control_writes, read_control, write_control_snapshot` |
| `tests/web/test_api_model_evidence.py` | `mpc_calibration_command_revision` |
| `tests/web/test_api_mpc_calibration.py` | `execute_control_writes, read_control, write_control_snapshot` |
| `tests/web/test_api_probe_map.py` | `execute_control_writes, read_control, write_control_snapshot` |
| `tests/web/test_api_settings_update.py` | `execute_control_writes, read_control, write_control_snapshot` |
| `tests/web/test_api_tuner.py` | `execute_control_writes, read_control, write_control_snapshot` |
| `tests/web/test_api_tuner_auto.py` | `execute_control_writes, read_control, write_control_snapshot` |
| `tests/web/test_api_update.py` | `read_control, write_control_snapshot` |
| `tests/web/test_api_wizard.py` | `read_control, write_control_snapshot` |
| `tests/web/test_control_liveness_not_sticky.py` | `default_control, write_control_snapshot` |
| `tests/web/test_socketio_app_data.py` | `execute_control_writes, read_control, write_control_snapshot` |
| `tests/web/test_webapp_sqlite.py` | `write_control_snapshot` |
| `wizard.py` | `enqueue_control_delta, read_control` |

## `common.persistence.runtime`

| Importer | Symbols |
|---|---|
| `app.py` | `flush_errors, read_settings` |
| `blueprints/api/routes.py` | `clear_warnings_through, read_current, read_pellet_db, read_probe_status, read_settings, read_status, write_settings` |
| `blueprints/api_admin/routes.py` | `read_pellet_db, read_settings, write_pellet_db, write_settings` |
| `blueprints/api_history/routes.py` | `read_settings` |
| `blueprints/api_metrics/routes.py` | `read_settings` |
| `blueprints/api_tuner/routes.py` | `read_current_snapshot, read_settings, read_tr, write_settings` |
| `blueprints/api_update/routes.py` | `read_settings` |
| `blueprints/api_wizard/routes.py` | `read_settings, write_settings` |
| `blueprints/mobile/socket_io.py` | `CONTROL_HEARTBEAT_STALE_AFTER, flush_connected_users, read_connected_users, read_control_heartbeat, read_current, read_errors, read_generic_key, read_pellets_store, read_settings_store, read_status, read_warnings_snapshot, remove_connected_user, seed_pellets_store, seed_settings_store, write_connected_user, write_pellet_db` |
| `blueprints/wizard/wizard.py` | `read_settings` |
| `board-config.py` | `read_settings` |
| `common/api_commands.py` | `read_current, read_current_snapshot, read_pellet_db, read_settings, read_status, write_settings` |
| `common/app.py` | `read_settings, write_settings` |
| `common/backups.py` | `read_pellet_db, read_settings, write_pellet_db, write_warning` |
| `common/controller_model_state.py` | `read_generic_key, write_generic_key` |
| `common/datastore.py` | `read_settings` |
| `common/defaults.py` | `read_settings` |
| `common/pellets_actions.py` | `write_pellet_db` |
| `common/settings_migration.py` | `write_settings_store, write_warning` |
| `common/system.py` | `read_settings` |
| `controller/model_learning/report.py` | `read_status` |
| `controller/pid_sp_learning.py` | `read_status` |
| `controller/runtime/devices.py` | `read_pellet_db, write_errors, write_generic_key, write_pellet_db` |
| `controller/runtime/heartbeat.py` | `CONTROL_HEARTBEAT_KEY` |
| `controller/runtime/runner.py` | `read_errors, write_errors` |
| `display/_base_flex.py` | `read_current, read_settings, read_status, write_settings` |
| `display/qtapp.py` | `read_current, read_settings_store, read_status` |
| `display/qtquick_flex.py` | `read_status` |
| `display_launch.py` | `read_settings` |
| `file_mgmt/cookfile.py` | `read_settings` |
| `file_mgmt/recipes.py` | `read_settings` |
| `notify/notifications.py` | `read_pellet_db, read_settings, write_settings` |
| `tests/characterization/test_all_writers_strict.py` | `init_status, read_settings, write_pellet_db, write_settings_store` |
| `tests/characterization/test_control_delta_seam.py` | `write_settings_store` |
| `tests/characterization/test_control_writes_cross_writer.py` | `read_settings, write_settings_store` |
| `tests/characterization/test_process_command_golden.py` | `flush_current, init_status, read_current, read_settings, read_status, write_settings, write_settings_store, write_status` |
| `tests/e2e/test_work_cycle_e2e.py` | `read_settings` |
| `tests/ui/test_qtquick_dispatch_persistence.py` | `init_status, read_settings, read_status, write_settings_store, write_status` |
| `tests/unit/bootstrap/test_startup_migration.py` | `read_pellet_db, read_settings, write_pellets_store, write_settings_store` |
| `tests/unit/common/test_common_blobs.py` | `clear_warnings_through, flush_connected_users, flush_errors, read_connected_users, read_errors, read_generic_key, read_probe_status, read_status, read_warnings_snapshot, remove_connected_user, write_connected_user, write_errors, write_generic_key, write_status, write_warning` |
| `tests/unit/common/test_import_smoke.py` | `read_probe_status, read_settings` |
| `tests/unit/common/test_install_status.py` | `datastore` |
| `tests/unit/common/test_json_blob_helpers.py` | `_read_json_blob, _write_json_blob, datastore` |
| `tests/unit/common/test_pellets_migration_v2.py` | `read_pellets_store, write_pellets_store` |
| `tests/unit/common/test_pellets_schema.py` | `read_pellets_store, write_pellet_db` |
| `tests/unit/common/test_pellets_writers_v2.py` | `read_pellets_store, write_pellets_store` |
| `tests/unit/common/test_settings_migration.py` | `read_settings, write_settings_store` |
| `tests/unit/common/test_settings_migration_matrix.py` | `read_settings_store, write_settings, write_settings_store` |
| `tests/unit/common/test_settings_migration_retired_controllers.py` | `read_settings_store, write_settings_store` |
| `tests/unit/common/test_settings_schema.py` | `write_settings_store` |
| `tests/unit/common/test_write_settings_strict.py` | `read_settings, write_settings, write_settings_store` |
| `tests/unit/controller/test_heartbeat.py` | `CONTROL_HEARTBEAT_KEY, CONTROL_HEARTBEAT_STALE_AFTER` |
| `tests/unit/controller/test_pid_sp_learning.py` | `read_status` |
| `tests/unit/datastore/test_control_trace_store.py` | `CONTROL_HEARTBEAT_KEY, CONTROL_HEARTBEAT_STALE_AFTER` |
| `tests/unit/datastore/test_current_accessors.py` | `flush_current, read_current, read_current_snapshot, read_settings, write_current, write_settings` |
| `tests/unit/datastore/test_pellets_shape_migration.py` | `read_pellets_store, write_pellets_store` |
| `tests/unit/datastore/test_read_path_validation.py` | `read_settings, write_settings_store` |
| `tests/unit/datastore/test_settings_shape_migration.py` | `read_settings_store, write_settings, write_settings_store` |
| `tests/unit/datastore/test_settings_store_migration.py` | `read_settings, read_settings_store, write_settings, write_settings_store` |
| `tests/unit/datastore/test_sqlite_store_parity.py` | `time, write_settings` |
| `tests/unit/persistence/test_domain_contracts.py` | `clear_warnings_through, read_warnings_snapshot, write_warning` |
| `tests/unit/runtime/test_devices.py` | `read_errors, read_pellet_db, write_errors` |
| `tests/unit/wizard/test_platform_pin_types.py` | `read_settings, write_settings_store` |
| `tests/unit/wizard/test_wizard_legacy_settings.py` | `read_settings, write_settings_store` |
| `tests/unit/wizard/test_wizard_probe_rename.py` | `read_settings, write_settings_store` |
| `tests/unit/wizard/test_wizard_run_no_probes.py` | `write_settings_store` |
| `tests/web/archive_builders.py` | `read_settings` |
| `tests/web/conftest.py` | `init_status, read_settings, write_pellets_store, write_settings_store` |
| `tests/web/test_api_admin_backups.py` | `read_pellet_db, read_pellets_store, read_settings` |
| `tests/web/test_api_admin_maintenance.py` | `read_pellet_db, read_settings, write_pellet_db` |
| `tests/web/test_api_admin_system.py` | `read_pellet_db, read_settings, write_pellet_db, write_settings` |
| `tests/web/test_api_dismiss_warnings.py` | `read_warnings_snapshot, write_warning` |
| `tests/web/test_api_history.py` | `read_settings` |
| `tests/web/test_api_metrics.py` | `read_settings, write_settings` |
| `tests/web/test_api_model_evidence.py` | `read_settings, read_status, write_settings, write_status` |
| `tests/web/test_api_mpc_calibration.py` | `read_settings, write_settings` |
| `tests/web/test_api_pellets.py` | `read_pellets_store` |
| `tests/web/test_api_pid_sp_learning.py` | `read_status, write_generic_key` |
| `tests/web/test_api_probe_map.py` | `read_settings, write_settings_store` |
| `tests/web/test_api_settings_controller_gate.py` | `read_settings, write_settings` |
| `tests/web/test_api_settings_update.py` | `read_settings, write_settings` |
| `tests/web/test_api_tuner.py` | `read_settings` |
| `tests/web/test_api_tuner_auto.py` | `write_current` |
| `tests/web/test_api_update.py` | `read_settings, write_settings_store` |
| `tests/web/test_api_wizard.py` | `read_settings, write_settings_store` |
| `tests/web/test_control_liveness_not_sticky.py` | `CONTROL_HEARTBEAT_KEY, CONTROL_HEARTBEAT_STALE_AFTER, init_status, read_errors, read_pellets_store, read_settings_store, write_errors, write_generic_key, write_pellet_db, write_settings_store` |
| `tests/web/test_socket_dash_payload_fields.py` | `flush_current, init_status, read_pellet_db, read_settings, read_status, write_generic_key, write_settings, write_status` |
| `tests/web/test_socket_probe_staleness.py` | `flush_current, init_status, read_current, read_pellet_db, read_settings, write_current, write_generic_key` |
| `tests/web/test_socket_ui_hash.py` | `flush_current, init_status, read_pellet_db, read_settings, read_status, write_generic_key, write_status` |
| `tests/web/test_socket_warnings_payload.py` | `flush_current, init_status, read_pellet_db, read_settings, write_generic_key, write_warning` |
| `tests/web/test_socketio_app_data.py` | `CONTROL_HEARTBEAT_KEY, CONTROL_HEARTBEAT_STALE_AFTER, flush_current, init_status, read_connected_users, read_current, read_errors, read_pellets_store, read_settings, read_status, write_connected_user, write_errors, write_generic_key, write_pellet_db, write_settings_store` |
| `tests/web/test_webapp_sqlite.py` | `init_status, read_connected_users, read_current, read_settings, remove_connected_user, write_connected_user, write_current, write_generic_key, write_pellets_store, write_settings_store` |
| `tools/emc2301_tach_diag.py` | `read_settings` |
| `tools/thermoworks_list.py` | `read_settings` |
| `updater.py` | `read_settings, write_settings` |
| `web-react/tests/e2e/seed_history.py` | `read_settings` |
| `wizard.py` | `read_settings, write_settings` |

## `common.persistence.history`

| Importer | Symbols |
|---|---|
| `blueprints/api_admin/routes.py` | `flush_history` |
| `blueprints/api_metrics/routes.py` | `read_all_metrics` |
| `blueprints/api_tuner/routes.py` | `autotune_length, flush_autotune, read_autotune, write_autotune` |
| `blueprints/mobile/socket_io.py` | `flush_history` |
| `common/app.py` | `read_all_metrics, read_history` |
| `file_mgmt/cookfile.py` | `flush_history, read_all_metrics, read_history` |
| `notify/notifications.py` | `read_history` |
| `tests/oracle/capture_oracle.py` | `append_metric, read_all_metrics, read_history, read_metrics, update_metrics, write_history` |
| `tests/unit/common/test_common_blobs.py` | `autotune_length, flush_autotune, read_autotune, read_history, write_autotune, write_history` |
| `tests/unit/common/test_common_history.py` | `read_history, write_history` |
| `tests/unit/common/test_common_metrics.py` | `append_metric, read_all_metrics, read_metrics, update_metrics` |
| `tests/unit/datastore/test_datastore.py` | `read_history` |
| `tests/unit/file_mgmt/test_cookfile.py` | `append_metric, read_all_metrics, read_history, update_metrics, write_history` |
| `tests/web/test_api_metrics.py` | `append_metric, flush_metrics, update_metrics` |
| `tests/web/test_api_tuner.py` | `write_tr` |
| `tests/web/test_api_tuner_auto.py` | `flush_autotune, read_autotune, write_autotune, write_tr` |
| `tests/web/test_webapp_sqlite.py` | `read_history, write_history` |

## `common.persistence.control_trace`

| Importer | Symbols |
|---|---|
| `controller/control_trace_replay.py` | `read_control_trace_session` |
| `controller/mpc.py` | `append_control_trace` |
| `controller/runtime/control_trace_recorder.py` | `append_control_trace, prune_control_trace, prune_incompatible_control_trace` |
| `controller/update_mpc.py` | `read_control_trace_cook, read_control_trace_session` |
| `tests/unit/common/test_model_evidence_store.py` | `append_control_trace, prune_control_trace` |
| `tests/unit/mpc/test_model_evidence_report.py` | `read_control_trace_session` |
| `tests/unit/mpc/test_mpc_calibration.py` | `append_control_trace` |
| `tests/unit/mpc/test_update_mpc.py` | `append_control_trace` |
| `tests/unit/runtime/test_hold_control_trace.py` | `read_control_trace_session` |

## `common.persistence.install_state`

| Importer | Symbols |
|---|---|
| `blueprints/api_update/routes.py` | `get_updater_install_status, set_updater_install_status` |
| `blueprints/api_wizard/routes.py` | `delete_wizard_install_info, get_wizard_install_status, load_wizard_install_info, set_wizard_install_status, store_wizard_install_info` |
| `common/system.py` | `load_os_info, store_os_info` |
| `tests/unit/common/test_common_blobs.py` | `get_wizard_install_status, set_wizard_install_status` |
| `tests/unit/common/test_install_status.py` | `get_updater_install_status, get_wizard_install_status, set_updater_install_status, set_wizard_install_status` |
| `tests/unit/common/test_os_info_read_path_is_pure.py` | `load_os_info, store_os_info` |
| `tests/unit/persistence/test_domain_contracts.py` | `delete_wizard_install_info, get_updater_install_status, get_wizard_install_status, load_os_info, load_wizard_install_info, set_updater_install_status, set_wizard_install_status, store_wizard_install_info` |
| `tests/unit/updater/test_acados_build.py` | `get_updater_install_status` |
| `tests/unit/wizard/test_platform_pin_types.py` | `get_wizard_install_status` |
| `tests/unit/wizard/test_wizard_run_no_probes.py` | `get_wizard_install_status` |
| `tests/web/test_api_wizard.py` | `load_wizard_install_info, store_wizard_install_info` |
| `updater.py` | `set_updater_install_status, set_wizard_install_status` |
| `wizard.py` | `load_wizard_install_info, set_updater_install_status, set_wizard_install_status` |

## `common.persistence.model_evidence`

| Importer | Symbols |
|---|---|
| _No remaining monolith importers_ | _Already migrated_ |

## `controller.model_learning.migration`

| Importer | Symbols |
|---|---|
| _No remaining monolith importers_ | _Already migrated_ |

## Cutover Order

1. Control and runtime blobs; run characterization control tests, runtime store/device tests, current/settings/pellet/status tests, SQLite parity, and the real-store work-cycle E2E.
2. History/metrics/autotune and install state; run common history/metrics/blob/install tests, file management, tuner/metrics, updater, wizard, and OS-info tests.
3. Control trace; run control-trace recorder/replay/update/MPC/Hold trace tests.
4. Model evidence and controller migration policy; run model persistence/report/activation/migration and model-evidence web tests.
5. Import smoke and deletion; remove `common/datastore_accessors.py`, run LSP workspace references plus all-form AST import search, then run the aggregate persistence/runtime/web suites.

Each numbered cutover is a sequential Jujutsu change. Do not run these caller migrations concurrently because several importers consume more than one destination domain.
