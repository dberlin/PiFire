import QtQuick
import ".."

Rectangle {
	id: card
	property string timerText: ""
	visible: timerText.length > 0
	height: 60
	radius: Theme.radius
	color: Theme.surface
	Text {
		anchors.centerIn: parent
		text: "⏱ " + card.timerText
		color: Theme.text
		font.pixelSize: 34
	}
}
