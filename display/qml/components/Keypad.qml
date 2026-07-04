import QtQuick
import QtQuick.Layouts
import ".."

Item {
	id: kp
	property int value: 0
	property string title: ""
	property string units: "F"
	signal accepted(int v)
	signal cancelled()

	ColumnLayout {
		anchors.centerIn: parent
		spacing: 16
		Text {
			text: kp.title
			color: Theme.subtext
			font.pixelSize: 32
			Layout.alignment: Qt.AlignHCenter
		}
		Text {
			text: kp.value + "°" + kp.units
			color: Theme.text
			font.pixelSize: 76
			font.bold: true
			Layout.alignment: Qt.AlignHCenter
		}
		GridLayout {
			columns: 3
			columnSpacing: 12
			rowSpacing: 12
			Layout.alignment: Qt.AlignHCenter
			Repeater {
				model: ["1", "2", "3", "4", "5", "6", "7", "8", "9", "C", "0", "OK"]
				MenuButton {
					Layout.preferredWidth: 130
					Layout.preferredHeight: 96
					text: modelData
					accent: modelData === "OK" ? Theme.ok : (modelData === "C" ? Theme.danger : Theme.primary)
					onClicked: {
						if (modelData === "OK")
							kp.accepted(kp.value);
						else if (modelData === "C")
							kp.value = 0;
						else
							kp.value = Math.min(999, kp.value * 10 + parseInt(modelData));
					}
				}
			}
		}
		MenuButton {
			text: "Cancel"
			Layout.fillWidth: true
			accent: Theme.subtext
			onClicked: kp.cancelled()
		}
	}
}
