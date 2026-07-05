import QtQuick
import QtQuick.Layouts
import ".."
import "../components"

Item {
	id: dash
	// name "" opens the mode-appropriate main menu; a specific name opens that menu.
	signal requestMenu(string name)
	signal requestInput(string name, string origin)

	RowLayout {
		anchors.fill: parent
		anchors.margins: 24
		spacing: 24

		// Left column: food probes (tap to set notify target)
		ColumnLayout {
			Layout.preferredWidth: 320
			Layout.fillHeight: true
			spacing: 16
			Repeater {
				model: backend.foodProbes
				CompactGauge {
					Layout.fillWidth: true
					Layout.preferredHeight: 130
					label: model.name
					probeName: model.name
					value: model.temp
					target: model.target
					maxValue: model.maxTemp
					units: backend.units
					onTapped: dash.requestInput("notify", model.name)
				}
			}
			Item { Layout.fillHeight: true }
		}

		// Center column: mode bar, primary gauge, timer/alert, control panel
		ColumnLayout {
			Layout.fillWidth: true
			Layout.fillHeight: true
			spacing: 16
			ModeBar {
				Layout.fillWidth: true
				mode: backend.modeText
				onClicked: dash.requestMenu("")
			}
			Gauge {
				Layout.fillWidth: true
				Layout.fillHeight: true
				label: backend.primaryName
				probeName: backend.primaryName
				value: backend.primaryTemp
				setpoint: backend.primarySetpoint
				target: backend.primaryNotifyTarget
				maxValue: backend.primaryMax
				units: backend.units
				onTapped: dash.requestInput("notify", backend.primaryName)
			}
			TimerCard {
				Layout.fillWidth: true
				timerText: backend.timerText
				timerLabel: backend.timerLabel
			}
			Alert {
				Layout.fillWidth: true
				shown: backend.lidOpen
				message: "LID OPEN"
			}
			ControlPanel {
				Layout.fillWidth: true
				mode: backend.mode
				recipe: backend.recipe
				recipePaused: backend.recipePaused
				onOpenMenu: (name) => dash.requestMenu(name)
				onOpenInput: (name, origin) => dash.requestInput(name, origin)
			}
		}

		// Right column: menu, status icons, controls, hopper
		ColumnLayout {
			Layout.preferredWidth: 170
			Layout.fillHeight: true
			spacing: 16
			MenuButton {
				text: "☰"
				Layout.fillWidth: true
				onClicked: dash.requestMenu("")
			}
			RowLayout {
				Layout.fillWidth: true
				spacing: 8
				StatusIcon {
					label: "Fan"
					active: backend.fanOn
					onClicked: backend.toggleFan()
				}
				StatusIcon {
					label: "Aug"
					active: backend.augerOn
					onClicked: backend.toggleAuger()
				}
			}
			StatusIcon {
				label: "Ign"
				active: backend.igniterOn
				onClicked: backend.toggleIgniter()
			}
			PModeControl {
				Layout.fillWidth: true
				pMode: backend.pMode
				active: backend.pModeActive
				onClicked: dash.requestMenu("pmode")
			}
			SmokePlusControl {
				Layout.fillWidth: true
				active: backend.smokePlus
				onClicked: backend.toggleSmokePlus()
			}
			Item { Layout.fillHeight: true }
			HopperStatus {
				Layout.fillWidth: true
				level: backend.hopperLevel
				hopperEnabled: backend.hopperEnabled
				onClicked: backend.hopperCheck()
			}
		}
	}
}
