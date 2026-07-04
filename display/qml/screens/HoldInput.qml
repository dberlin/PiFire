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
	Keypad {
		anchors.fill: parent
		title: "Enter Hold Temperature"
		units: backend.units
		value: Math.round(backend.primarySetpoint) || 200
		onAccepted: (v) => {
			backend.setHold(v);
			screen.close();
		}
		onCancelled: screen.close()
	}
}
