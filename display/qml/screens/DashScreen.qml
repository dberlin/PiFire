import QtQuick
import QtQuick.Layouts
import ".."
import "../components"

// Ember dashboard: header bar + 3-column body (food probes / primary gauge +
// cook-time + controls / system + duty pills + hopper). Assembles the
// components built in Tasks 8-14 against the real `backend`. See
// .superpowers/sdd/task-15-brief.md and tools/qt_dashboard_preview.qml for
// the verified layout-sizing rules this structure follows: nested Layouts
// compute their own implicit size from children, which overrides advisory
// Layout.preferredWidth/Height, so every fixed-size region below is pinned
// with matching minimum/maximum constraints, and only the gauge card and the
// hopper card absorb vertical slack via Layout.fillHeight.
Item {
	id: dash
	// name "" opens the mode-appropriate main menu; a specific name opens that menu.
	signal requestMenu(string name)
	signal requestInput(string name, string origin)

	property bool hold: backend.mode === "Hold"
	readonly property bool compact: width <= 1100

	ColumnLayout {
		anchors.fill: parent
		spacing: 0

		HeaderBar {
			Layout.fillWidth: true
			compact: dash.compact
			onMenuRequested: dash.requestMenu("")
		}

		RowLayout {
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
						name: model.name
						temp: model.temp
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
						value: backend.primaryTemp
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
						label: dash.hold ? "AUGER DUTY" : "P-MODE"
						value: dash.hold ? backend.augerDuty + "%" : "P-" + backend.pMode
						highlighted: false
					}
					DutyPill {
						Layout.fillWidth: true
						Layout.fillHeight: true
						compact: dash.compact
						label: dash.hold ? "FAN DUTY" : "SMOKE+"
						value: dash.hold ? backend.fanDuty + "%" : (backend.smokePlus ? "ON" : "OFF")
						highlighted: dash.hold ? backend.fanOn : backend.smokePlus
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
	}
}
