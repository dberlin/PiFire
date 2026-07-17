import QtQuick
import ".."

Rectangle {
	id: ctrl
	property bool active: false
	signal clicked()
	height: 60
	radius: Theme.radius
	color: active ? Theme.ok : Theme.surface
	Behavior on color { ColorAnimation { duration: Theme.animMs } }
	PressOverlay { pressed: ctrlMouse.pressed }
	Text {
		anchors.centerIn: parent
		text: "Smoke+"
		color: Theme.text
		font.pixelSize: 26
	}
	MouseArea {
		id: ctrlMouse
		anchors.fill: parent
		onClicked: ctrl.clicked()
	}
}
