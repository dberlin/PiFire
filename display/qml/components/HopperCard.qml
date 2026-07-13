import QtQuick
import ".."

// Hopper level card: header (HOPPER label + n% in threshold color), a
// vertical fill bar anchored to the bottom of a track, and a threshold
// status label. Sized by the caller's Layout (DashScreen, Task 15) — this
// component does not bind its own width/height. Adapted from the
// preview-verified hopperCard in tools/qt_dashboard_preview.qml.
Rectangle {
	id: card
	property bool compact: false

	signal checkRequested()

	// D1: whole card is hidden when the pellet sensor is disabled.
	visible: backend.hopperEnabled

	readonly property real level: backend.hopperLevel
	readonly property color hopCol: level < 15 ? Theme.dangerColor : level < 35 ? Theme.warn : Theme.okColor

	color: Theme.card
	radius: Theme.cardRadius
	border.color: Theme.cardBorder

	TapHandler { onTapped: card.checkRequested() }

	Column {
		anchors.fill: parent
		anchors.margins: card.compact ? 12 : 16
		spacing: 12

		// header: "HOPPER" label (left) + n% (right) via anchors — no width feedback
		Item {
			width: parent.width
			height: pct.implicitHeight
			Text {
				anchors.left: parent.left
				anchors.verticalCenter: parent.verticalCenter
				text: "HOPPER"
				font.family: Theme.sans
				font.pixelSize: 13
				font.letterSpacing: 2.5
				color: Theme.label
			}
			Text {
				id: pct
				anchors.right: parent.right
				anchors.verticalCenter: parent.verticalCenter
				text: Math.round(card.level) + "%"
				font.family: Theme.condensed
				font.pixelSize: card.compact ? 26 : 34
				font.bold: true
				color: card.hopCol
			}
		}

		Rectangle {
			id: track
			width: parent.width
			height: parent.height - (card.compact ? 60 : 78)
			radius: 14
			color: Qt.rgba(1, 1, 1, 0.045)
			border.color: Qt.rgba(1, 1, 1, 0.04)
			clip: true
			Rectangle {
				anchors.bottom: parent.bottom
				width: parent.width
				height: track.height * card.level / 100
				color: card.hopCol
				Behavior on height { NumberAnimation { duration: 900; easing.type: Easing.OutCubic } }
			}
		}

		Text {
			text: card.level < 15 ? "REFILL PELLETS" : card.level < 35 ? "RUNNING LOW" : "LEVEL OK"
			font.family: Theme.sans
			font.pixelSize: card.compact ? 11 : 12
			font.letterSpacing: 2
			color: card.hopCol
		}
	}
}
