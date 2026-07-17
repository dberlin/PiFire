import QtQuick
import ".."

// Food-probe card: name, target string (→ N° / AMBIENT), big temp, progress
// bar. Sized by the caller's Layout (DashScreen's food-probe Repeater, Task
// 15) — this component does not bind its own width/height. Adapted from the
// preview-verified left-column probe card in tools/qt_dashboard_preview.qml.
Rectangle {
	id: card
	property bool compact: false
	property string name: ""
	property real temp: 0
	property real target: 0
	property real maxTemp: 300
	property string units: "F"
	signal tapped()

	readonly property bool done: target > 0 && temp >= target - 1

	color: Theme.card
	radius: Theme.cardRadius
	border.color: Theme.cardBorder

	TapHandler { id: tap; onTapped: card.tapped() }
	PressOverlay { pressed: tap.pressed }

	Column {
		anchors.verticalCenter: parent.verticalCenter
		anchors.left: parent.left
		anchors.right: parent.right
		anchors.leftMargin: 18
		anchors.rightMargin: 18
		spacing: 4

		// header: name (left) + target (right) via anchors — no width feedback
		Item {
			width: parent.width
			height: nameText.implicitHeight
			Text {
				id: nameText
				anchors.left: parent.left
				anchors.verticalCenter: parent.verticalCenter
				text: card.name.toUpperCase()
				font.family: Theme.sans
				font.pixelSize: card.compact ? 13 : 15
				font.letterSpacing: 1.5
				color: Theme.probeLabel
			}
			Text {
				anchors.right: parent.right
				anchors.verticalCenter: parent.verticalCenter
				text: card.target > 0 ? "→ " + card.target + "°" : "AMBIENT"
				font.family: Theme.sans
				font.pixelSize: card.compact ? 13 : 15
				color: card.target > 0 ? (card.done ? Theme.okColor : Theme.cookingColor) : Theme.label
			}
		}

		Row {
			spacing: 2
			Text {
				text: Math.round(card.temp)
				font.family: Theme.condensed
				font.pixelSize: card.compact ? 52 : 66
				font.bold: true
				color: Theme.textColor
			}
			Text {
				text: "°" + card.units
				font.family: Theme.condensed
				font.pixelSize: card.compact ? 20 : 26
				color: Theme.dim
				anchors.bottom: parent.bottom
				anchors.bottomMargin: 8
			}
		}

		Rectangle {
			width: parent.width
			height: 6
			radius: 3
			color: Qt.rgba(1, 1, 1, 0.11)
			Rectangle {
				height: parent.height
				radius: 3
				width: parent.width * (card.target > 0 ? Math.max(0.02, Math.min(1, card.temp / card.target)) : 0)
				color: card.done ? Theme.okColor : Theme.accentColor
				Behavior on width { NumberAnimation { duration: 900; easing.type: Easing.OutCubic } }
			}
		}
	}
}
