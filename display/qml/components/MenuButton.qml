import QtQuick
import QtQuick.Controls
import ".."

Button {
	id: b
	property color accent: Theme.primary
	implicitHeight: 96
	font.pixelSize: 34
	contentItem: Text {
		text: b.text
		color: Theme.text
		font: b.font
		horizontalAlignment: Text.AlignHCenter
		verticalAlignment: Text.AlignVCenter
		elide: Text.ElideRight
	}
	background: Rectangle {
		radius: Theme.radius
		color: b.down ? Qt.darker(b.accent, 1.4) : Theme.surface
		border.color: b.accent
		border.width: b.activeFocus ? 4 : 2
	}
}
