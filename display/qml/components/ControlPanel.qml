import QtQuick
import QtQuick.Layouts
import ".."

RowLayout {
	spacing: 16
	signal openMenu()
	MenuButton {
		text: "Prime"
		accent: Theme.primary
		Layout.fillWidth: true
		onClicked: openMenu()
	}
	MenuButton {
		text: "Startup"
		accent: Theme.ok
		Layout.fillWidth: true
		onClicked: backend.startup()
	}
	MenuButton {
		text: "Monitor"
		accent: Theme.primary
		Layout.fillWidth: true
		onClicked: backend.monitor()
	}
	MenuButton {
		text: "Stop"
		accent: Theme.danger
		Layout.fillWidth: true
		onClicked: backend.stop()
	}
}
