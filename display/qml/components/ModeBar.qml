import QtQuick
import ".."

Rectangle {
	id: bar
	property string mode: "Stop"
	signal clicked()
	height: 60
	radius: Theme.radius
	color: Theme.surface
	Text {
		anchors.centerIn: parent
		text: bar.mode
		color: Theme.primary
		font.pixelSize: 34
		font.bold: true
	}
	MouseArea {
		anchors.fill: parent
		onClicked: bar.clicked()
	}
}
