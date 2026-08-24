"""
*****************************************
PiFire Qt Quick Display — Backend Bridge
*****************************************

 Description: QObject bridge between PiFire's Redis-backed data/command layer
 and the Qt Quick (QML) UI. Polls live data via an injected fetch function and
 exposes it as Qt properties; forwards UI actions via an injected command
 function. Framework-agnostic and unit-testable without a running QML engine.

*****************************************
"""

from collections.abc import Mapping
import math
import time

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QObject,
    Property,
    QPersistentModelIndex,
    Qt,
    Signal,
    Slot,
)
from pydantic import ValidationError

from common.modes import Mode
from common.persistence.runtime import CONTROL_HEARTBEAT_STALE_AFTER
from common.web_contracts.core import ThermocoupleHealthView
from display.staleness import resolve_reading


class FoodProbeModel(QAbstractListModel):
    NameRole = Qt.UserRole + 1
    TempRole = Qt.UserRole + 2
    TargetRole = Qt.UserRole + 3
    MaxRole = Qt.UserRole + 4
    HasTempRole = Qt.UserRole + 5
    StaleRole = Qt.UserRole + 6

    def __init__(self, food_info, parent=None):
        super().__init__(parent)
        # Live data (F/NT) is keyed by probe *label*; the display name and the
        # notify origin use probe *name* (matching the pygame flex display).
        self._rows = [
            {
                "name": f.get("name", f"Probe {i + 1}"),
                "label": f.get("label", f.get("name", f"Probe {i + 1}")),
                "temp": 0,
                "hasTemp": True,
                "stale": "",
                "target": 0,
                "maxTemp": f.get("max_temp", 300),
            }
            for i, f in enumerate(food_info)
        ]

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def roleNames(self):
        return {
            self.NameRole: b"name",
            self.TempRole: b"temp",
            self.TargetRole: b"target",
            self.MaxRole: b"maxTemp",
            self.HasTempRole: b"hasTemp",
            self.StaleRole: b"stale",
        }

    def data(self, index, role):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        return {
            self.NameRole: row["name"],
            self.TempRole: row["temp"],
            self.TargetRole: row["target"],
            self.MaxRole: row["maxTemp"],
            self.HasTempRole: row["hasTemp"],
            self.StaleRole: row["stale"],
        }.get(role)

    def update(self, in_data, now_ms=None, *, invalid_labels=None):
        f = in_data.get("F", {})
        nt = in_data.get("NT", {})
        last = in_data.get("LAST", {})
        if now_ms is None:
            now_ms = int(time.time() * 1000)
        invalid_labels = invalid_labels or set()
        changed = False
        for row in self._rows:
            # .get with no default on purpose: a probe reporting None and a
            # probe whose key is missing are the same thing to the card, and
            # `f.get(label, 0)` only defaulted the second.
            if row["label"] in invalid_labels:
                temp, has_temp, stale = 0.0, False, ""
            else:
                temp, has_temp, stale = resolve_reading(f.get(row["label"]), last.get(row["label"]), now_ms)
            target = nt.get(row["label"], 0)
            if (row["temp"], row["hasTemp"], row["stale"], row["target"]) != (temp, has_temp, stale, target):
                row["temp"], row["hasTemp"], row["stale"], row["target"] = temp, has_temp, stale, target
                changed = True
        if changed and self._rows:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._rows) - 1, 0),
                [self.TempRole, self.TargetRole, self.HasTempRole, self.StaleRole],
            )


def _finite_float(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) else None


def project_thermocouple_health(settings, probe_device_info, controller_mode, *, now=None):
    """Adapt producer reports to the validated shared health wire projection."""
    if not isinstance(probe_device_info, list) or not isinstance(settings, Mapping):
        return []
    probe_settings = settings.get("probe_settings")
    if not isinstance(probe_settings, Mapping):
        return []
    probe_map = probe_settings.get("probe_map")
    if not isinstance(probe_map, Mapping):
        return []
    configured_probes = probe_map.get("probe_info")
    if not isinstance(configured_probes, list):
        return []

    reports_by_probe = {}
    for device_info in probe_device_info:
        if not isinstance(device_info, Mapping):
            continue
        device = device_info.get("device")
        status = device_info.get("status")
        if not isinstance(device, str) or not isinstance(status, Mapping):
            continue
        reports = status.get("thermocouple_health")
        if not isinstance(reports, Mapping):
            continue
        for label, report in reports.items():
            if isinstance(label, str) and isinstance(report, Mapping):
                reports_by_probe[(device, label)] = report

    now = time.time() if now is None else now
    now = _finite_float(now)
    if now is None:
        return []

    projected = []
    for probe in configured_probes:
        if not isinstance(probe, Mapping):
            continue
        device = probe.get("device")
        port = probe.get("port")
        label = probe.get("label")
        display_name = probe.get("name")
        role = probe.get("type")
        if (
            not isinstance(device, str)
            or not isinstance(port, str)
            or not isinstance(label, str)
            or not isinstance(display_name, str)
            or role not in {"Primary", "Food", "Aux"}
        ):
            continue

        report = reports_by_probe.get((device, label))
        if report is None:
            continue
        observed_at = _finite_float(report.get("observed_at"))
        detail = report.get("detail")
        evidence = report.get("evidence")
        if observed_at is None or not isinstance(detail, Mapping) or not isinstance(evidence, list):
            continue
        policy = detail.get("policy")
        if policy not in {"off", "observe", "enforce"}:
            continue

        age_s = max(0.0, now - observed_at)
        has_hardware = "hardware" in evidence
        has_software = any(item != "hardware" for item in evidence)
        source = "mixed" if has_hardware and has_software else "hardware" if has_hardware else "software"

        state = report.get("state")
        temperature_valid = report.get("temperature_valid")
        outcome = "none"
        if state == "confirmed":
            if role == "Primary" and temperature_valid is True:
                outcome = "notify_only"
            elif role == "Primary" and controller_mode == Mode.ERROR:
                outcome = "stopped"
            else:
                outcome = "unavailable"

        try:
            view = ThermocoupleHealthView.model_validate(
                {
                    "device": device,
                    "port": port,
                    "label": label,
                    "displayName": display_name,
                    "role": role,
                    "report": {
                        "state": state,
                        "faults": report.get("faults"),
                        "evidence": evidence,
                        "temperatureValid": temperature_valid,
                        "detail": dict(detail),
                    },
                    "detector": {"source": source, "policy": policy},
                    "outcome": outcome,
                    "freshness": {
                        "current": age_s <= CONTROL_HEARTBEAT_STALE_AFTER,
                        "lastReportedAgeS": age_s,
                    },
                },
                strict=True,
            )
        except ValidationError:
            continue
        projected.append(view.model_dump(mode="json", by_alias=True, exclude_none=False))
    return projected


class ProbeHealthModel(QAbstractListModel):
    summaryChanged = Signal()

    DeviceRole = int(Qt.ItemDataRole.UserRole) + 1
    PortRole = int(Qt.ItemDataRole.UserRole) + 2
    LabelRole = int(Qt.ItemDataRole.UserRole) + 3
    DisplayNameRole = int(Qt.ItemDataRole.UserRole) + 4
    ProbeRole = int(Qt.ItemDataRole.UserRole) + 5
    StateRole = int(Qt.ItemDataRole.UserRole) + 6
    FaultsRole = int(Qt.ItemDataRole.UserRole) + 7
    EvidenceRole = int(Qt.ItemDataRole.UserRole) + 8
    TemperatureValidRole = int(Qt.ItemDataRole.UserRole) + 9
    SourceRole = int(Qt.ItemDataRole.UserRole) + 10
    PolicyRole = int(Qt.ItemDataRole.UserRole) + 11
    OutcomeRole = int(Qt.ItemDataRole.UserRole) + 12
    SeverityRole = int(Qt.ItemDataRole.UserRole) + 13
    AvailabilityRole = int(Qt.ItemDataRole.UserRole) + 14
    HeadlineRole = int(Qt.ItemDataRole.UserRole) + 15
    ImpactCopyRole = int(Qt.ItemDataRole.UserRole) + 16
    CauseCopyRole = int(Qt.ItemDataRole.UserRole) + 17
    SourceCopyRole = int(Qt.ItemDataRole.UserRole) + 18
    PriorityRole = int(Qt.ItemDataRole.UserRole) + 19
    FreshnessCurrentRole = int(Qt.ItemDataRole.UserRole) + 20
    LastReportedAgeRole = int(Qt.ItemDataRole.UserRole) + 21
    FreshnessQualifierRole = int(Qt.ItemDataRole.UserRole) + 22

    _ROLE_NAMES = {
        DeviceRole: QByteArray(b"device"),
        PortRole: QByteArray(b"port"),
        LabelRole: QByteArray(b"label"),
        DisplayNameRole: QByteArray(b"displayName"),
        ProbeRole: QByteArray(b"role"),
        StateRole: QByteArray(b"state"),
        FaultsRole: QByteArray(b"faults"),
        EvidenceRole: QByteArray(b"evidence"),
        TemperatureValidRole: QByteArray(b"temperatureValid"),
        SourceRole: QByteArray(b"source"),
        PolicyRole: QByteArray(b"policy"),
        OutcomeRole: QByteArray(b"outcome"),
        SeverityRole: QByteArray(b"severity"),
        AvailabilityRole: QByteArray(b"availability"),
        HeadlineRole: QByteArray(b"headline"),
        ImpactCopyRole: QByteArray(b"impactCopy"),
        CauseCopyRole: QByteArray(b"causeCopy"),
        SourceCopyRole: QByteArray(b"sourceCopy"),
        PriorityRole: QByteArray(b"priority"),
        FreshnessCurrentRole: QByteArray(b"freshnessCurrent"),
        LastReportedAgeRole: QByteArray(b"lastReportedAgeS"),
        FreshnessQualifierRole: QByteArray(b"freshnessQualifier"),
    }

    _SOURCE_COPY = {
        "hardware": "Hardware",
        "software": "Software",
        "mixed": "Hardware + software",
    }
    _FAULT_ORDER = ("open", "short", "malfunction")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []
        self._summary = {}

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def roleNames(self) -> dict[int, QByteArray]:
        return self._ROLE_NAMES

    def data(self, index, role: int = int(Qt.ItemDataRole.DisplayRole)):
        if not index.isValid() or index.row() < 0 or index.row() >= len(self._rows):
            return None
        name = self._ROLE_NAMES.get(role)
        return self._rows[index.row()].get(bytes(name.data()).decode()) if name is not None else None

    @staticmethod
    def _presentation(state, outcome):
        if state in {"unmonitored", "healthy"}:
            return "quiet", None, None, 0
        if state == "suspected":
            return "warning", "CHECK PROBE", "Possible thermocouple issue; reading still available.", 1
        if outcome == "stopped":
            return "danger", "CONTROL PROBE UNAVAILABLE", "PiFire stopped heating.", 4
        if outcome == "notify_only":
            return "danger", "FAULT", "Fault detected — Observe mode did not stop heating.", 3
        if outcome == "unavailable":
            return "danger", "PROBE UNAVAILABLE", "Grill control continues.", 2
        return "danger", "FAULT", None, 2

    @classmethod
    def _project(cls, item):
        try:
            wire = ThermocoupleHealthView.model_validate(item, strict=True)
        except (TypeError, ValidationError):
            return None
        raw = wire.model_dump(mode="json", by_alias=True, exclude_none=False)
        report = raw["report"]
        detector = raw["detector"]
        freshness = raw["freshness"]
        state = report["state"]
        outcome = raw["outcome"]
        severity, headline, impact_copy, priority = cls._presentation(state, outcome)
        faults = [fault for fault in cls._FAULT_ORDER if fault in report["faults"]]
        causes = []
        if state == "confirmed":
            if "open" in faults:
                causes.append("Hardware reported an open circuit.")
            if "short" in faults:
                causes.append("Hardware reported a short circuit.")
            if "malfunction" in faults:
                causes.append("Software detected an abnormal thermocouple response.")
        unavailable = (
            outcome in {"stopped", "unavailable"}
            or (state == "confirmed" and report["temperatureValid"] is False)
        )
        return {
            "device": raw["device"],
            "port": raw["port"],
            "label": raw["label"],
            "displayName": raw["displayName"],
            "role": raw["role"],
            "state": state,
            "faults": faults,
            "evidence": list(report["evidence"]),
            "temperatureValid": report["temperatureValid"],
            "source": detector["source"],
            "policy": detector["policy"],
            "outcome": outcome,
            "severity": severity,
            "availability": "unavailable" if unavailable else "current",
            "headline": headline,
            "impactCopy": impact_copy,
            "causeCopy": " ".join(causes) if causes else None,
            "sourceCopy": cls._SOURCE_COPY[detector["source"]],
            "priority": priority,
            "freshnessCurrent": freshness["current"],
            "lastReportedAgeS": freshness["lastReportedAgeS"],
            "freshnessQualifier": None if freshness["current"] else "Last reported",
        }

    @staticmethod
    def _summarize(rows):
        highest = None
        issue_count = 0
        for row in rows:
            if row["priority"] == 0:
                continue
            issue_count += 1
            if highest is None or row["priority"] > highest["priority"]:
                highest = row
        if highest is None:
            return {}
        additional_count = issue_count - 1
        return {
            "highest": dict(highest),
            "additionalCount": additional_count,
            "additionalCopy": f"+{additional_count} more" if additional_count else None,
        }

    def update(self, health):
        rows = []
        if isinstance(health, list):
            for item in health:
                row = self._project(item)
                if row is not None:
                    rows.append(row)
        summary = self._summarize(rows)
        if rows == self._rows:
            return
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()
        self._summary = summary
        self.summaryChanged.emit()

    def invalid_labels(self):
        return {
            row["label"]
            for row in self._rows
            if row["state"] == "confirmed" and row["temperatureValid"] is False
        }

    @Property(dict, notify=summaryChanged)
    def summary(self):
        return self._summary


class PiFireBackend(QObject):
    modeChanged = Signal()
    modeTextChanged = Signal()
    unitsChanged = Signal()
    primaryChanged = Signal()
    hopperChanged = Signal()
    statusChanged = Signal()
    timerChanged = Signal()
    asleepChanged = Signal()
    navEvent = Signal(str)
    accentThemeChanged = Signal()

    HEALTH_POLL_SECONDS = 1.0

    def __init__(
        self,
        fetch_fn,
        command_fn,
        probe_info,
        accent_fn=None,
        timeout_fn=None,
        parent=None,
        health_fetch_fn=None,
    ):
        super().__init__(parent)
        self._fetch_fn = fetch_fn
        self._command_fn = command_fn
        self._probe_info = probe_info or {}
        self._now = time.time
        self._accent_fn = accent_fn
        self._timeout_fn = timeout_fn
        self._health_fetch_fn = health_fetch_fn
        self._last_health_check = None
        self._accent_theme = "Ember"
        self._last_settings_check = 0.0
        primary = self._probe_info.get("primary", {})
        self._primary_name = primary.get("name", "Primary")
        self._primary_label = primary.get("label", self._primary_name)
        self._primary_max = primary.get("max_temp", 600)
        self._primary_notify = 0
        self._ip_address = self._probe_info.get("ip_address", "") or ""
        self._food_model = FoodProbeModel(self._probe_info.get("food", []))
        self._health_model = ProbeHealthModel(self)
        self._mode = "Stop"
        self._units = "F"
        self._primary_temp = 0
        self._primary_has_temp = True
        self._primary_stale = ""
        self._primary_sp = 0
        self._hopper_level = 0
        self._hopper_enabled = False
        self._p_mode = 0
        self._s_plus = False
        self._fan = False
        self._auger = False
        self._igniter = False
        self._lid_open = False
        self._recipe = False
        self._recipe_paused = False
        self._timer_text = ""
        self._timer_label = ""
        self._mode_text = "Stop"
        self._p_mode_active = False
        self._auger_duty = 0
        self._fan_duty = 0
        self._food_count = len(self._probe_info.get("food", []))
        self._cook_elapsed_text = "00:00"
        # Idle / sleep state
        self.TIMEOUT = self._timeout_fn() if self._timeout_fn is not None else 300
        self._last_interaction = self._now()
        self._asleep = False

    def _set(self, attr, value, signal):
        if getattr(self, attr) != value:
            setattr(self, attr, value)
            signal.emit()

    def _poll_health(self, now):
        if self._health_fetch_fn is None:
            return
        if self._last_health_check is not None and now - self._last_health_check < self.HEALTH_POLL_SECONDS:
            return
        self._last_health_check = now
        try:
            health = self._health_fetch_fn()
        except Exception:
            health = None
        self._health_model.update(health)

    @Slot()
    def poll(self):
        in_data, status = self._fetch_fn()
        if status is None or in_data is None:
            return
        self._set("_mode", status.get("mode", Mode.STOP), self.modeChanged)
        self._set("_units", status.get("units", "F"), self.unitsChanged)
        now = self._now()
        self._poll_health(now)
        invalid_labels = self._health_model.invalid_labels()
        p = in_data.get("P", {})
        primary_key = next(iter(p), self._primary_label)
        now_ms = int(now * 1000)
        if primary_key in invalid_labels:
            primary_temp, primary_has_temp, primary_stale = 0.0, False, ""
        else:
            primary_temp, primary_has_temp, primary_stale = resolve_reading(
                p.get(primary_key),
                in_data.get("LAST", {}).get(primary_key),
                now_ms,
            )
        self._set("_primary_temp", primary_temp, self.primaryChanged)
        self._set("_primary_has_temp", primary_has_temp, self.primaryChanged)
        self._set("_primary_stale", primary_stale, self.primaryChanged)
        self._set("_primary_sp", in_data.get("PSP", 0) or 0, self.primaryChanged)
        nt = in_data.get("NT", {})
        self._set("_primary_notify", nt.get(primary_key, 0) or 0, self.primaryChanged)
        outpins = status.get("outpins", {})
        self._set("_fan", bool(outpins.get("fan", False)), self.statusChanged)
        self._set("_auger", bool(outpins.get("auger", False)), self.statusChanged)
        self._set("_igniter", bool(outpins.get("igniter", False)), self.statusChanged)
        self._set("_p_mode", status.get("p_mode", 0), self.statusChanged)
        self._set("_s_plus", bool(status.get("s_plus", False)), self.statusChanged)
        self._set("_auger_duty", int(round((status.get("cycle_ratio", 0) or 0) * 100)), self.statusChanged)
        self._set("_fan_duty", int(status.get("fan_duty", 0) or 0), self.statusChanged)
        self._set("_lid_open", bool(status.get("lid_open_detected", False)), self.statusChanged)
        self._set("_recipe", bool(status.get("recipe", False)), self.statusChanged)
        self._set("_recipe_paused", bool(status.get("recipe_paused", False)), self.statusChanged)
        self._set("_hopper_enabled", bool(status.get("hopper_level_enabled", False)), self.hopperChanged)
        self._set("_hopper_level", max(status.get("hopper_level", 0) or 0, 0), self.hopperChanged)
        self._food_model.update(in_data, now_ms, invalid_labels=invalid_labels)
        self._update_timer_text(status, now)
        self._update_cook_elapsed(status, now)
        mode = status.get("mode", Mode.STOP)
        recipe = bool(status.get("recipe", False))
        mode_text = f"Recipe: {mode}" if recipe and mode != Mode.SHUTDOWN else mode
        self._set("_mode_text", mode_text, self.modeTextChanged)
        self._set("_p_mode_active", mode in (Mode.STARTUP, Mode.REIGNITE, Mode.SMOKE), self.statusChanged)
        if (now - self._last_settings_check) >= 1.0:
            self._last_settings_check = now
            if self._accent_fn is not None:
                self._set("_accent_theme", self._accent_fn() or "Ember", self.accentThemeChanged)
            if self._timeout_fn is not None:
                self.TIMEOUT = self._timeout_fn()
        self._update_idle(mode, now)

    def _update_timer_text(self, status, now):
        mode = status.get("mode", Mode.STOP)
        duration_key = {
            "Startup": "start_duration",
            "Reignite": "start_duration",
            "Prime": "prime_duration",
            "Shutdown": "shutdown_duration",
        }.get(mode)
        text = ""
        label = ""
        if duration_key and status.get("start_time"):
            remaining = int(status.get(duration_key, 0) - (now - status["start_time"]))
            remaining = max(remaining, 0)
            text = f"{remaining // 60:02d}:{remaining % 60:02d}"
            label = "Timer"
        elif mode == Mode.HOLD and status.get("lid_open_detected") and status.get("lid_open_endtime"):
            remaining = max(int(status["lid_open_endtime"] - now), 0)
            text = f"{remaining // 60:02d}:{remaining % 60:02d}"
            label = "Lid Pause"
        self._set("_timer_text", text, self.timerChanged)
        self._set("_timer_label", label, self.timerChanged)

    def _update_cook_elapsed(self, status, now):
        ts = status.get("startup_timestamp", 0) or 0
        if ts and status.get("mode", Mode.STOP) not in (Mode.STOP, Mode.MONITOR):
            secs = max(int(now - ts), 0)
            h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
            text = (f"{h}:" if h else "") + f"{m:02d}:{s:02d}"
        else:
            text = "00:00"
        self._set("_cook_elapsed_text", text, self.timerChanged)

    def _update_idle(self, mode, now):
        # The screen never sleeps during an active cook; in Stop it sleeps after
        # TIMEOUT seconds of no interaction. TIMEOUT <= 0 disables sleeping and
        # wakes an already-asleep screen. Leaving Stop auto-wakes.
        if mode != Mode.STOP:
            self._set("_asleep", False, self.asleepChanged)
        elif self.TIMEOUT <= 0:
            self._set("_asleep", False, self.asleepChanged)
        elif now - self._last_interaction > self.TIMEOUT:
            self._set("_asleep", True, self.asleepChanged)

    @Slot()
    def registerInteraction(self):
        self._last_interaction = self._now()
        self._set("_asleep", False, self.asleepChanged)

    # ---------------- Action slots ----------------
    @Slot(str, int)
    @Slot(str)
    def action(self, command, value=0):
        self._command_fn(command, value)

    @Slot()
    def startup(self):
        self.action("cmd_startup")

    @Slot()
    def stop(self):
        self.action("cmd_stop")

    @Slot()
    def monitor(self):
        self.action("cmd_monitor")

    @Slot()
    def shutdown(self):
        self.action("cmd_shutdown")

    @Slot()
    def smoke(self):
        self.action("cmd_smoke")

    @Slot()
    def toggleSmokePlus(self):
        self.action("cmd_splus")

    @Slot()
    def nextStep(self):
        self.action("cmd_next_step")

    @Slot(int)
    def setHold(self, temp):
        self.action("cmd_hold", int(temp))

    @Slot(str, int)
    def setNotify(self, origin, target):
        self._command_fn("cmd_notify", {"origin": origin, "target": int(target)})

    @Slot(int)
    def setPMode(self, n):
        self.action("cmd_pmode", int(n))

    @Slot(int)
    def primeStartup(self, grams):
        self.action("cmd_primestartup", int(grams))

    @Slot(int)
    def primeOnly(self, grams):
        self.action("cmd_primeonly", int(grams))

    @Slot()
    def reboot(self):
        self.action("cmd_reboot")

    @Slot()
    def powerOff(self):
        self.action("cmd_poweroff")

    @Slot()
    def restart(self):
        self.action("cmd_restart")

    @Slot()
    def hopperCheck(self):
        self.action("cmd_hopper_level")

    @Slot()
    def toggleFan(self):
        self.action("cmd_fan_toggle")

    @Slot()
    def toggleAuger(self):
        self.action("cmd_auger_toggle")

    @Slot()
    def toggleIgniter(self):
        self.action("cmd_igniter_toggle")

    @Slot()
    def toggleLidOpen(self):
        self.action("cmd_lid_open")

    @Slot()
    def navUp(self):
        self.navEvent.emit("UP")

    @Slot()
    def navDown(self):
        self.navEvent.emit("DOWN")

    @Slot()
    def navEnter(self):
        self.navEvent.emit("ENTER")

    # ---------------- Properties ----------------
    @Property(str, notify=modeChanged)
    def mode(self):
        return self._mode

    @Property(str, notify=modeTextChanged)
    def modeText(self):
        return self._mode_text

    @Property(bool, notify=statusChanged)
    def pModeActive(self):
        return self._p_mode_active

    @Property(bool, notify=asleepChanged)
    def asleep(self):
        return self._asleep

    @Property(str, notify=unitsChanged)
    def units(self):
        return self._units

    @Property(float, notify=primaryChanged)
    def primaryTemp(self):
        return float(self._primary_temp)

    @Property(bool, notify=primaryChanged)
    def primaryHasTemp(self):
        return self._primary_has_temp

    @Property(str, notify=primaryChanged)
    def primaryStale(self):
        return self._primary_stale

    @Property(float, notify=primaryChanged)
    def primarySetpoint(self):
        return float(self._primary_sp)

    @Property(str, constant=True)
    def primaryName(self):
        return self._primary_name

    @Property(float, notify=primaryChanged)
    def primaryNotifyTarget(self):
        return float(self._primary_notify)

    @Property(float, constant=True)
    def primaryMax(self):
        return float(self._primary_max)

    @Property(str, constant=True)
    def ipAddress(self):
        return self._ip_address

    @Property(QObject, constant=True)
    def foodProbes(self):
        return self._food_model

    @Property(QObject, constant=True)
    def probeHealth(self):
        return self._health_model

    @Property(int, notify=hopperChanged)
    def hopperLevel(self):
        return int(self._hopper_level)

    @Property(bool, notify=hopperChanged)
    def hopperEnabled(self):
        return self._hopper_enabled

    @Property(int, notify=statusChanged)
    def pMode(self):
        return self._p_mode

    @Property(int, notify=statusChanged)
    def augerDuty(self):
        return self._auger_duty

    @Property(int, notify=statusChanged)
    def fanDuty(self):
        return self._fan_duty

    @Property(int, constant=True)
    def foodProbeCount(self):
        return self._food_count

    @Property(str, notify=timerChanged)
    def cookElapsedText(self):
        return self._cook_elapsed_text

    @Property(bool, notify=statusChanged)
    def smokePlus(self):
        return self._s_plus

    @Property(bool, notify=statusChanged)
    def fanOn(self):
        return self._fan

    @Property(bool, notify=statusChanged)
    def augerOn(self):
        return self._auger

    @Property(bool, notify=statusChanged)
    def igniterOn(self):
        return self._igniter

    @Property(bool, notify=statusChanged)
    def lidOpen(self):
        return self._lid_open

    @Property(bool, notify=statusChanged)
    def recipe(self):
        return self._recipe

    @Property(bool, notify=statusChanged)
    def recipePaused(self):
        return self._recipe_paused

    @Property(str, notify=timerChanged)
    def timerText(self):
        return self._timer_text

    @Property(str, notify=timerChanged)
    def timerLabel(self):
        return self._timer_label

    @Property(str, notify=accentThemeChanged)
    def accentTheme(self):
        return self._accent_theme
