import QtQuick
import ".."
import "../components"

Item {
	id: screen
	property string origin: ""
	signal close()
	Rectangle {
		anchors.fill: parent
		color: Qt.rgba(0, 0, 0, 0.85)
	}
	Keypad {
		anchors.fill: parent
		title: "Notify Target: " + screen.origin
		units: backend.units
		value: 0
		onAccepted: (v) => {
			backend.setNotify(screen.origin, v);
			screen.close();
		}
		onCancelled: screen.close()
	}
}
