import QtQuick
import QtQuick.Layouts
import ".."
import "../components"
import "../Menus.js" as Menus

Item {
	id: screen
	property string menuName: "main"
	readonly property var menu: Menus.menuFor(menuName)
	signal close()
	signal openMenu(string name)
	signal openInput(string name)

	Rectangle {
		anchors.fill: parent
		color: Qt.rgba(0, 0, 0, 0.6)
		MouseArea { anchors.fill: parent }  // swallow taps outside the card
	}

	Rectangle {
		anchors.centerIn: parent
		width: Math.min(parent.width - 120, 720)
		height: Math.min(parent.height - 80, contentCol.implicitHeight + 48)
		radius: Theme.radius
		color: Theme.background
		border.color: Theme.primary
		border.width: 2

		ColumnLayout {
			id: contentCol
			anchors.fill: parent
			anchors.margins: 24
			spacing: 12
			Text {
				text: screen.menu.title
				color: Theme.text
				font.pixelSize: 40
				font.bold: true
				Layout.alignment: Qt.AlignHCenter
			}
			Flickable {
				Layout.fillWidth: true
				Layout.fillHeight: true
				contentHeight: itemsCol.implicitHeight
				clip: true
				ColumnLayout {
					id: itemsCol
					width: parent.width
					spacing: 10
					Repeater {
						model: screen.menu.items
						MenuButton {
							Layout.fillWidth: true
							text: modelData.label
							accent: modelData.action === "cmd_stop" ? Theme.danger : Theme.primary
							onClicked: screen.activate(modelData)
						}
					}
				}
			}
		}
	}

	function activate(item) {
		var a = item.action;
		if (a === "menu_close") {
			screen.close();
			return;
		}
		if (a.indexOf("menu_") === 0) {
			screen.openMenu(a.substring(5));
			return;
		}
		if (a.indexOf("input_") === 0) {
			screen.openInput(a.substring(6));
			return;
		}
		backend.action(a, item.value !== undefined ? item.value : 0);
		screen.close();
	}
}
