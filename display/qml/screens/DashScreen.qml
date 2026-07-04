import QtQuick
import QtQuick.Layouts
import ".."
import "../components"

Item {
	id: dash
	signal openMenu()

	RowLayout {
		anchors.fill: parent
		anchors.margins: 24
		spacing: 24

		// Left column: food probes
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
					value: model.temp
					target: model.target
					maxValue: model.maxTemp
					units: backend.units
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
				mode: backend.mode
				onClicked: dash.openMenu()
			}
			Gauge {
				Layout.fillWidth: true
				Layout.fillHeight: true
				label: backend.primaryName
				value: backend.primaryTemp
				setpoint: backend.primarySetpoint
				maxValue: backend.primaryMax
				units: backend.units
			}
			TimerCard {
				Layout.fillWidth: true
				timerText: backend.timerText
			}
			Alert {
				Layout.fillWidth: true
				shown: backend.lidOpen
				message: "LID OPEN"
			}
			ControlPanel {
				Layout.fillWidth: true
				onOpenMenu: dash.openMenu()
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
				onClicked: dash.openMenu()
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
				onClicked: dash.openMenu()
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
