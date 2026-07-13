import QtQuick
import QtQuick.Layouts
import ".."

Rectangle {
	id: bar
	property bool compact: false
	color: Theme.card
	radius: bar.compact ? 12 : 14
	border.color: Qt.rgba(1, 1, 1, 0.06)
	border.width: 1

	RowLayout {
		anchors.fill: parent
		anchors.leftMargin: 20
		anchors.rightMargin: 20
		spacing: 12

		Text {
			id: cookTimeLabel
			objectName: "cookTimeLabel"
			text: backend.timerText.length > 0 ? backend.timerLabel : "COOK TIME"
			font.family: Theme.sans
			font.pixelSize: bar.compact ? 11 : 12
			font.letterSpacing: 2
			color: Theme.label
		}

		Item { Layout.fillWidth: true }

		Text {
			id: cookTimeValue
			objectName: "cookTimeValue"
			text: backend.timerText.length > 0 ? backend.timerText : backend.cookElapsedText
			font.family: Theme.condensed
			font.pixelSize: bar.compact ? 20 : 26
			font.bold: true
			color: backend.timerText.length > 0 ? Theme.textColor : Theme.dim
		}
	}
}
