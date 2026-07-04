import QtQuick
import ".."

Rectangle {
	id: alert
	property string message: ""
	property bool shown: false
	visible: shown
	height: 60
	radius: Theme.radius
	color: Theme.danger
	Text {
		anchors.centerIn: parent
		text: alert.message
		color: "white"
		font.pixelSize: 30
		font.bold: true
	}
}
