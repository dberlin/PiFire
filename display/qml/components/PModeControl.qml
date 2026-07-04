import QtQuick
import ".."

Rectangle {
	id: ctrl
	property int pMode: 0
	signal clicked()
	height: 60
	radius: Theme.radius
	color: Theme.surface
	Text {
		anchors.centerIn: parent
		text: "P-" + ctrl.pMode
		color: Theme.text
		font.pixelSize: 30
	}
	MouseArea {
		anchors.fill: parent
		onClicked: ctrl.clicked()
	}
}
