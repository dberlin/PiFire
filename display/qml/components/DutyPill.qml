import QtQuick
import QtQuick.Layouts
import ".." as QmlGlobal

Rectangle {
	id: root
	radius: root.compact ? 12 : 14
	implicitWidth: 120
	implicitHeight: 64

	// Properties
	property bool compact: false
	property string label: ""
	property string value: ""
	property bool highlighted: false
	// A pill is a readout unless the caller gives it something to open. Set
	// per-pill rather than always-on, because what a pill shows depends on the
	// mode and only some of those readings have a control behind them.
	property bool clickable: false
	signal tapped()

	// Styling
	color: highlighted ? Qt.rgba(QmlGlobal.Theme.okColor.r, QmlGlobal.Theme.okColor.g, QmlGlobal.Theme.okColor.b, 0.14) : QmlGlobal.Theme.card
	border.color: highlighted ? QmlGlobal.Theme.okColor : QmlGlobal.Theme.cardBorder
	border.width: 1.5

	TapHandler {
		id: tap
		enabled: root.clickable
		onTapped: root.tapped()
	}
	PressOverlay { pressed: root.clickable && tap.pressed }

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
			font.pixelSize: root.compact ? 18 : 24
			font.bold: true
			color: root.highlighted ? QmlGlobal.Theme.okColor : QmlGlobal.Theme.accentColor
		}
	}
}
