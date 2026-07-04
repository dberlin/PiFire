import QtQuick
import ".."

Rectangle {
	property string label: ""
	property real value: 0
	property real target: 0
	property real maxValue: 300
	property string units: "F"
	radius: Theme.radius
	color: Theme.surface
	Column {
		anchors.centerIn: parent
		spacing: 2
		Text {
			anchors.horizontalCenter: parent.horizontalCenter
			text: label
			color: Theme.subtext
			font.pixelSize: 22
		}
		Text {
			anchors.horizontalCenter: parent.horizontalCenter
			text: Math.round(value) + "°" + units
			color: Theme.text
			font.pixelSize: 48
			font.bold: true
		}
		Text {
			anchors.horizontalCenter: parent.horizontalCenter
			visible: target > 0
			text: "→ " + Math.round(target) + "°"
			color: Theme.notify
			font.pixelSize: 20
		}
	}
}
