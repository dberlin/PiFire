import QtQuick
import QtQuick.Layouts
import ".."

Rectangle {
	id: alert
	property string message: ""
	property bool shown: false
	visible: shown
	Layout.preferredWidth: 210
	Layout.fillHeight: true
	radius: 14
	color: Qt.rgba(Theme.dangerColor.r, Theme.dangerColor.g, Theme.dangerColor.b, 0.14)
	border.color: Theme.dangerColor
	border.width: 1.5

	Text {
		anchors.centerIn: parent
		text: alert.message
		font.family: Theme.sans
		font.pixelSize: 20
		font.bold: true
		font.letterSpacing: 2
		color: Theme.dangerColor
	}

	SequentialAnimation on opacity {
		running: alert.shown
		loops: Animation.Infinite
		NumberAnimation { to: 0.4; duration: 500 }
		NumberAnimation { to: 1.0; duration: 500 }
	}
}
