import QtQuick
import ".."

Rectangle {
	id: ctrl
	property int pMode: 0
	property bool active: true
	signal clicked()
	height: 60
	radius: Theme.radius
	color: Theme.surface
	opacity: active ? 1.0 : 0.4
	Behavior on opacity { NumberAnimation { duration: Theme.animMs } }
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
