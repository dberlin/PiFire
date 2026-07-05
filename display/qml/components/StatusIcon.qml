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

	// Subtle pulse while the output is active (parity with pygame animation).
	SequentialAnimation on opacity {
		running: icon.active
		loops: Animation.Infinite
		alwaysRunToEnd: true
		NumberAnimation { to: 0.55; duration: 600; easing.type: Easing.InOutQuad }
		NumberAnimation { to: 1.0; duration: 600; easing.type: Easing.InOutQuad }
	}
	onActiveChanged: if (!active) opacity = 1.0
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
