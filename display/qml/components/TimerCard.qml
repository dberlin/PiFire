import QtQuick
import ".."

Rectangle {
	id: card
	property string timerText: ""
	property string timerLabel: ""
	visible: timerText.length > 0
	height: 60
	radius: Theme.radius
	color: Theme.surface
	Text {
		anchors.centerIn: parent
		text: (card.timerLabel ? card.timerLabel + "  " : "⏱ ") + card.timerText
		color: Theme.text
		font.pixelSize: 34
	}
}
