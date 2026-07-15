import QtQuick
import QtQuick.Layouts
import ".."

Item {
	id: kp
	property int value: 0
	property string title: ""
	property string units: "F"
	// Same width threshold DashScreen.qml uses to pick its compact layout;
	// the keypad fills its parent screen (anchors.fill: parent at every call
	// site), so `width` here is the actual screen width. At the 1024x600
	// profile the full-size keypad's total height overflows the 600px-tall
	// screen and clips the Cancel button, so shrink fonts/spacing/buttons
	// to fit when the screen is this narrow (also short, for every profile
	// this repo ships).
	readonly property bool compact: width <= 1100
	// `value` starts out showing the current/default setpoint (e.g. 200), not
	// anything the user has typed. Without this, the first digit pressed
	// appended onto that starting value instead of replacing it -- typing
	// "4" over a displayed "200" produced 200*10+4 (clamped to 999) instead
	// of just "4". Track whether the user has actually typed a digit yet, so
	// the first press overwrites the starting value and only later presses
	// append.
	property bool typing: false
	signal accepted(int v)
	signal cancelled()

	function pressDigit(d) {
		kp.value = kp.typing ? Math.min(999, kp.value * 10 + d) : d;
		kp.typing = true;
	}
	function pressClear() {
		kp.value = 0;
		kp.typing = true;
	}

	ColumnLayout {
		id: layout
		objectName: "keypadColumn"
		anchors.centerIn: parent
		spacing: kp.compact ? 10 : 16
		Text {
			text: kp.title
			color: Theme.subtext
			font.pixelSize: kp.compact ? 24 : 32
			Layout.alignment: Qt.AlignHCenter
		}
		Text {
			text: kp.value + "°" + kp.units
			color: Theme.text
			font.pixelSize: kp.compact ? 54 : 76
			font.bold: true
			Layout.alignment: Qt.AlignHCenter
		}
		GridLayout {
			columns: 3
			columnSpacing: kp.compact ? 8 : 12
			rowSpacing: kp.compact ? 8 : 12
			Layout.alignment: Qt.AlignHCenter
			Repeater {
				model: ["1", "2", "3", "4", "5", "6", "7", "8", "9", "C", "0", "OK"]
				MenuButton {
					Layout.preferredWidth: kp.compact ? 104 : 130
					Layout.preferredHeight: kp.compact ? 66 : 96
					text: modelData
					accent: modelData === "OK" ? Theme.ok : (modelData === "C" ? Theme.danger : Theme.primary)
					onClicked: {
						if (modelData === "OK")
							kp.accepted(kp.value);
						else if (modelData === "C")
							kp.pressClear();
						else
							kp.pressDigit(parseInt(modelData));
					}
				}
			}
		}
		MenuButton {
			text: "Cancel"
			Layout.fillWidth: true
			Layout.preferredHeight: kp.compact ? 60 : implicitHeight
			accent: Theme.subtext
			onClicked: kp.cancelled()
		}
	}
}
