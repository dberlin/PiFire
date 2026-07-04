import QtQuick
import ".."

Rectangle {
	id: icon
	property string label: ""
	property bool active: false
	signal clicked()
	width: 96
	height: 96
	radius: width / 2
	color: active ? Theme.ok : Theme.surface
	border.color: active ? Theme.ok : Theme.subtext
	border.width: 2
	Behavior on color { ColorAnimation { duration: Theme.animMs } }
	Text {
		anchors.centerIn: parent
		text: icon.label
		color: Theme.text
		font.pixelSize: 22
	}
	MouseArea {
		anchors.fill: parent
		onClicked: icon.clicked()
	}
}
