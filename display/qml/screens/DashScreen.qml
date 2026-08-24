import QtQuick
import QtQuick.Layouts
import ".."
import "../components"

// Ember dashboard: header bar + 3-column body (food probes / primary gauge +
// cook-time + controls / system + duty pills + hopper). Assembles the card
// components against the real `backend`. See tools/qt_dashboard_preview.qml
// for the verified layout-sizing rules this structure follows: nested
// Layouts compute their own implicit size from children, which overrides
// advisory Layout.preferredWidth/Height, so every fixed-size region below is
// pinned with matching minimum/maximum constraints, and only the gauge card
// and the hopper card absorb vertical slack via Layout.fillHeight.
Item {
	id: dash
	// name "" opens the mode-appropriate main menu; a specific name opens that menu.
	signal requestMenu(string name)
	signal requestInput(string name, string origin)

	// P-mode and Smoke+ describe the smoke cycle, so the duty pills carry them
	// only while that cycle is what the grill is doing. Everywhere else they
	// report settings that govern nothing running, so the pills show the
	// actuator duties, which are true in every mode. P-mode and Smoke+ stay
	// reachable from the menu (Menus.js, "main_active_normal").
	property bool smoking: backend.mode === "Smoke"
	readonly property bool compact: width <= 1100
	readonly property bool portrait: height > width

	ColumnLayout {
		anchors.fill: parent
		spacing: 0

		HeaderBar {
			Layout.fillWidth: true
			compact: dash.compact
			onMenuRequested: dash.requestMenu("")
		}

		RowLayout {
			objectName: "landscapeDashBody"
			visible: !dash.portrait
			Layout.fillWidth: true
			Layout.fillHeight: true
			Layout.leftMargin: dash.compact ? 14 : 18
			Layout.rightMargin: dash.compact ? 14 : 18
			Layout.topMargin: dash.compact ? 12 : 16
			Layout.bottomMargin: dash.compact ? 14 : 18
			spacing: dash.compact ? 14 : 16

			// ----- Left: food probes. Collapses (and the center column flexes
			// into the freed space) when there are no food probes. -----
			ColumnLayout {
				Layout.preferredWidth: dash.compact ? 238 : 298
				Layout.minimumWidth: dash.compact ? 238 : 298
				Layout.maximumWidth: dash.compact ? 238 : 298
				Layout.fillHeight: true
				spacing: 12
				visible: backend.foodProbeCount > 0

				Text {
					text: "FOOD PROBES"
					font.family: Theme.sans
					font.pixelSize: 13
					font.letterSpacing: 2.5
					color: Theme.label
					Layout.leftMargin: 4
				}

				Repeater {
					model: backend.foodProbes
					ProbeCard {
						Layout.fillWidth: true
						Layout.fillHeight: true
						compact: dash.compact
						healthModel: backend.probeHealth
						name: model.name
						temp: model.temp
						hasTemp: model.hasTemp
						stale: model.stale
						target: model.target
						maxTemp: model.maxTemp
						units: backend.units
						onTapped: dash.requestInput("notify", model.name)
					}
				}
			}

			// ----- Center: primary gauge (absorbs vertical slack), cook-time +
			// lid alert row, control-panel buttons. Absorbs horizontal slack. -----
			ColumnLayout {
				Layout.fillWidth: true
				Layout.minimumWidth: dash.compact ? 300 : 380
				Layout.fillHeight: true
				spacing: 14

				Rectangle {
					Layout.fillWidth: true
					Layout.fillHeight: true
					Layout.minimumHeight: dash.compact ? 300 : 420
					color: Theme.card
					radius: Theme.cardRadius
					border.color: Theme.cardBorder
					clip: true

					Gauge {
						anchors.centerIn: parent
						width: dash.compact ? 300 : 392
						height: dash.compact ? 300 : 392
						compact: dash.compact
						healthModel: backend.probeHealth
						value: backend.primaryTemp
						hasValue: backend.primaryHasTemp
						stale: backend.primaryStale
						setpoint: backend.primarySetpoint
						target: backend.primaryNotifyTarget
						maxValue: backend.primaryMax
						units: backend.units
						probeName: backend.primaryName
						modeLabel: backend.modeText
						onTapped: dash.requestInput("notify", backend.primaryName)
					}
				}

				RowLayout {
					Layout.fillWidth: true
					Layout.preferredHeight: dash.compact ? 42 : 52
					Layout.maximumHeight: dash.compact ? 42 : 52
					spacing: 14

					CookTimeBar {
						Layout.fillWidth: true
						Layout.fillHeight: true
						compact: dash.compact
					}

					Alert {
						shown: backend.lidOpen
						message: "LID OPEN"
					}
				}

				ControlPanel {
					Layout.fillWidth: true
					Layout.preferredHeight: dash.compact ? 74 : 82
					Layout.maximumHeight: dash.compact ? 74 : 82
					compact: dash.compact
					mode: backend.mode
					recipe: backend.recipe
					recipePaused: backend.recipePaused
					onOpenMenu: (name) => dash.requestMenu(name)
					onOpenInput: (name, origin) => dash.requestInput(name, origin)
				}
			}

			// ----- Right: system status, duty/mode pills, hopper. -----
			ColumnLayout {
				Layout.preferredWidth: dash.compact ? 240 : 300
				Layout.minimumWidth: dash.compact ? 240 : 300
				Layout.maximumWidth: dash.compact ? 240 : 300
				Layout.fillHeight: true
				spacing: 14

				SystemCard {
					Layout.fillWidth: true
					compact: dash.compact
				}

				RowLayout {
					Layout.fillWidth: true
					Layout.preferredHeight: dash.compact ? 40 : 64
					Layout.maximumHeight: dash.compact ? 40 : 64
					spacing: 14

					DutyPill {
						Layout.fillWidth: true
						Layout.fillHeight: true
						compact: dash.compact
						label: dash.smoking ? "P-MODE" : "AUGER DUTY"
						value: dash.smoking ? "P-" + backend.pMode : backend.augerDuty + "%"
						highlighted: false
						// Only while it reads P-MODE: an auger duty is a
						// readout with nothing to set, and the same pill
						// carries both.
						clickable: dash.smoking
						onTapped: dash.requestMenu("pmode")
					}
					DutyPill {
						Layout.fillWidth: true
						Layout.fillHeight: true
						compact: dash.compact
						label: dash.smoking ? "SMOKE+" : "FAN DUTY"
						value: dash.smoking ? (backend.smokePlus ? "ON" : "OFF") : backend.fanDuty + "%"
						highlighted: dash.smoking ? backend.smokePlus : backend.fanOn
						// Smoke+ is a toggle, so the pill writes straight
						// through instead of opening a menu. Outside Smoke the
						// same pill reads FAN DUTY, which toggles nothing.
						clickable: dash.smoking
						onTapped: backend.toggleSmokePlus()
					}
				}

				HopperCard {
					Layout.fillWidth: true
					Layout.fillHeight: true
					Layout.minimumHeight: dash.compact ? 140 : 180
					compact: dash.compact
					onCheckRequested: backend.hopperCheck()
				}
			}
		}

		Loader {
			visible: dash.portrait
			active: dash.portrait
			Layout.fillWidth: true
			Layout.fillHeight: true
			sourceComponent: Component {
				Flickable {
					id: portraitFlick
					objectName: "portraitDashFlick"
					contentWidth: width
					contentHeight: portraitColumn.implicitHeight
					flickableDirection: Flickable.VerticalFlick
					boundsBehavior: Flickable.StopAtBounds
					clip: true

			Column {
				id: portraitColumn
				x: 14
				width: portraitFlick.width - 28
				spacing: 14

				// Preserve room for Main's persistent health banner.
				Item {
					width: parent.width
					height: backend.probeHealth.summary && backend.probeHealth.summary.highest
						&& backend.probeHealth.summary.highest.state === "confirmed" ? 72 : 12
				}

				Rectangle {
					id: portraitGaugeCard
					objectName: "portraitGaugeCard"
					width: parent.width
					height: Math.min(width, 390)
					color: Theme.card
					radius: Theme.cardRadius
					border.color: Theme.cardBorder
					clip: true

					Gauge {
						anchors.centerIn: parent
						width: Math.min(parent.width, 360)
						height: width
						compact: true
						healthModel: backend.probeHealth
						value: backend.primaryTemp
						hasValue: backend.primaryHasTemp
						stale: backend.primaryStale
						setpoint: backend.primarySetpoint
						target: backend.primaryNotifyTarget
						maxValue: backend.primaryMax
						units: backend.units
						probeName: backend.primaryName
						modeLabel: backend.modeText
						onTapped: dash.requestInput("notify", backend.primaryName)
					}
				}

				RowLayout {
					width: parent.width
					height: 44
					spacing: 12
					CookTimeBar {
						Layout.fillWidth: true
						Layout.fillHeight: true
						compact: true
					}
					Alert {
						shown: backend.lidOpen
						message: "LID OPEN"
					}
				}

				ControlPanel {
					width: parent.width
					height: 76
					compact: true
					mode: backend.mode
					recipe: backend.recipe
					recipePaused: backend.recipePaused
					onOpenMenu: (name) => dash.requestMenu(name)
					onOpenInput: (name, origin) => dash.requestInput(name, origin)
				}

				Text {
					visible: backend.foodProbeCount > 0
					text: "FOOD PROBES"
					font.family: Theme.sans
					font.pixelSize: 13
					font.letterSpacing: 2.5
					color: Theme.label
				}

				Repeater {
					model: backend.foodProbes
					ProbeCard {
						width: portraitColumn.width
						height: 180
						compact: true
						healthModel: backend.probeHealth
						name: model.name
						temp: model.temp
						hasTemp: model.hasTemp
						stale: model.stale
						target: model.target
						maxTemp: model.maxTemp
						units: backend.units
						onTapped: dash.requestInput("notify", model.name)
					}
				}

				SystemCard {
					width: parent.width
					compact: true
				}

				RowLayout {
					width: parent.width
					height: 48
					spacing: 12
					DutyPill {
						Layout.fillWidth: true
						Layout.fillHeight: true
						compact: true
						label: dash.smoking ? "P-MODE" : "AUGER DUTY"
						value: dash.smoking ? "P-" + backend.pMode : backend.augerDuty + "%"
						highlighted: false
						clickable: dash.smoking
						onTapped: dash.requestMenu("pmode")
					}
					DutyPill {
						Layout.fillWidth: true
						Layout.fillHeight: true
						compact: true
						label: dash.smoking ? "SMOKE+" : "FAN DUTY"
						value: dash.smoking ? (backend.smokePlus ? "ON" : "OFF") : backend.fanDuty + "%"
						highlighted: dash.smoking ? backend.smokePlus : backend.fanOn
						clickable: dash.smoking
						onTapped: backend.toggleSmokePlus()
					}
				}

				HopperCard {
					width: parent.width
					height: 180
					compact: true
					onCheckRequested: backend.hopperCheck()
				}

				Item { width: parent.width; height: 14 }
			}
		}
			}
		}
	}
}
