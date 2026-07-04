import QtQuick
import ".."
import "../components"

Item {
	id: screen
	signal close()
	Rectangle {
		anchors.fill: parent
		color: Qt.rgba(0, 0, 0, 0.85)
	}
	Column {
		anchors.centerIn: parent
		spacing: 24
		Text {
			anchors.horizontalCenter: parent.horizontalCenter
			text: "Open the PiFire Web UI"
			color: Theme.text
			font.pixelSize: 40
		}
		Text {
			anchors.horizontalCenter: parent.horizontalCenter
			text: backend.ipAddress ? "http://" + backend.ipAddress : "(network address unavailable)"
			color: Theme.primary
			font.pixelSize: 44
			font.bold: true
		}
		MenuButton {
			anchors.horizontalCenter: parent.horizontalCenter
			width: 260
			text: "Close"
			onClicked: screen.close()
		}
	}
}
