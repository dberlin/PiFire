import QtQuick
import QtQuick.Layouts
import ".." as QmlGlobal

Rectangle {
	id: root
	radius: 14
	implicitWidth: 120
	implicitHeight: 64

	// Properties
	property string label: ""
	property string value: ""
	property bool highlighted: false

	// Styling
	color: highlighted ? Qt.rgba(QmlGlobal.Theme.okColor.r, QmlGlobal.Theme.okColor.g, QmlGlobal.Theme.okColor.b, 0.14) : QmlGlobal.Theme.card
	border.color: highlighted ? QmlGlobal.Theme.okColor : QmlGlobal.Theme.cardBorder
	border.width: 1.5

	Column {
		anchors.centerIn: parent
		spacing: 2

		Text {
			anchors.horizontalCenter: parent.horizontalCenter
			text: root.label
			font.family: QmlGlobal.Theme.sans
			font.pixelSize: 10
			font.letterSpacing: 1.5
			color: root.highlighted ? QmlGlobal.Theme.okColor : QmlGlobal.Theme.label
		}

		Text {
			anchors.horizontalCenter: parent.horizontalCenter
			text: root.value
			font.family: QmlGlobal.Theme.condensed
			font.pixelSize: 24
			font.bold: true
			color: root.highlighted ? QmlGlobal.Theme.okColor : QmlGlobal.Theme.accentColor
		}
	}
}
