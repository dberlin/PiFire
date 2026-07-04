import QtQuick
import ".."

Rectangle {
	id: hopper
	property int level: 0
	property bool hopperEnabled: false
	signal clicked()
	visible: hopperEnabled
	height: 44
	radius: Theme.radius
	color: Theme.surface
	clip: true
	Rectangle {
		anchors.left: parent.left
		anchors.top: parent.top
		anchors.bottom: parent.bottom
		width: parent.width * Math.max(0, Math.min(100, hopper.level)) / 100
		radius: Theme.radius
		color: hopper.level < 15 ? Theme.danger : Theme.ok
		Behavior on width { NumberAnimation { duration: Theme.animMs } }
	}
	Text {
		anchors.centerIn: parent
		text: "Hopper " + hopper.level + "%"
		color: Theme.text
		font.pixelSize: 22
	}
	MouseArea {
		anchors.fill: parent
		onClicked: hopper.clicked()
	}
}
