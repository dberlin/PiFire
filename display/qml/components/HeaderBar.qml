import QtQuick
import QtQuick.Layouts
import ".."

// Top header bar: live-cook dot, "PiFire" wordmark, "CONTROLLER" label, IP
// address, clock, and a hamburger button that requests the menu screen.
// Self-contained (58px tall, fills parent width); consumes backend.ipAddress
// and backend.mode for the live dot, and drives its own clock via a Timer.
Item {
	id: header
	property bool compact: false
	height: header.compact ? 50 : 58

	readonly property bool cooking: ["Startup", "Reignite", "Smoke", "Hold", "Recipe"].indexOf(backend.mode) >= 0
	property string clock: ""

	signal menuRequested()

	Timer {
		interval: 1000
		running: true
		repeat: true
		triggeredOnStart: true
		onTriggered: header.clock = Qt.formatTime(new Date(), "h:mm AP")
	}

	Rectangle {
		anchors.bottom: parent.bottom
		width: parent.width
		height: 1
		color: Qt.rgba(1, 1, 1, 0.06)
	}

	RowLayout {
		anchors.fill: parent
		anchors.leftMargin: 22
		anchors.rightMargin: 22

		Rectangle {
			width: 12; height: 12; radius: 6
			color: header.cooking ? Theme.okColor : Theme.label
			SequentialAnimation on opacity {
				loops: Animation.Infinite
				NumberAnimation { to: 0.35; duration: 1200; easing.type: Easing.InOutQuad }
				NumberAnimation { to: 1.0; duration: 1200; easing.type: Easing.InOutQuad }
			}
		}

		Text {
			text: "Pi<font color='" + Theme.accentColor + "'>Fire</font>"
			textFormat: Text.RichText
			font.family: Theme.sans; font.pixelSize: header.compact ? 16 : 20; font.bold: true; color: Theme.textColor
			Layout.leftMargin: 12
		}

		Text {
			text: "CONTROLLER"
			font.family: Theme.sans; font.pixelSize: header.compact ? 11 : 12; font.letterSpacing: 2
			color: Theme.label
			Layout.leftMargin: 10
		}

		Item { Layout.fillWidth: true }

		Text {
			text: backend.ipAddress
			font.family: Theme.sans; font.pixelSize: header.compact ? 11 : 13; color: Theme.dim
		}

		Text {
			text: header.clock
			font.family: Theme.condensed; font.pixelSize: header.compact ? 18 : 22; color: Theme.dim
			Layout.leftMargin: 18
		}

		Rectangle {
			Layout.leftMargin: 18
			width: header.compact ? 38 : 44; height: header.compact ? 38 : 44; radius: header.compact ? 10 : 12
			color: Theme.inset
			border.color: Qt.rgba(1, 1, 1, 0.08)
			Column {
				anchors.centerIn: parent
				spacing: 4
				Repeater {
					model: 3
					Rectangle { width: header.compact ? 16 : 20; height: 2; radius: 2; color: Theme.probeLabel }
				}
			}
			TapHandler { id: menuTap; onTapped: header.menuRequested() }
			PressOverlay { pressed: menuTap.pressed }
		}
	}
}
