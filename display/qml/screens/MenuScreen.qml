import QtQuick
import ".."
import "../components"
import "../Menus.js" as Menus
import "../Actions.js" as Actions

Item {
	id: screen
	property string menuName: "main"
	readonly property var menu: Menus.menuFor(menuName)
	signal close()
	signal openMenu(string name)
	signal openInput(string name, string origin)

	Rectangle {
		anchors.fill: parent
		color: Qt.rgba(0, 0, 0, 0.6)
		MouseArea { anchors.fill: parent }  // swallow taps outside the card
	}

	Rectangle {
		id: card
		anchors.centerIn: parent
		width: Math.min(parent.width - 120, 720)
		height: Math.min(parent.height - 80, 24 + titleText.height + 12 + buttonsCol.implicitHeight + 24)
		radius: Theme.radius
		color: Theme.background
		border.color: Theme.primary
		border.width: 2

		Text {
			id: titleText
			anchors.top: parent.top
			anchors.topMargin: 24
			anchors.horizontalCenter: parent.horizontalCenter
			text: screen.menu.title
			color: Theme.text
			font.pixelSize: 40
			font.bold: true
		}

		Flickable {
			id: flick
			anchors.top: titleText.bottom
			anchors.left: parent.left
			anchors.right: parent.right
			anchors.bottom: parent.bottom
			anchors.topMargin: 12
			anchors.leftMargin: 24
			anchors.rightMargin: 24
			anchors.bottomMargin: 24
			contentHeight: buttonsCol.implicitHeight
			clip: true
			boundsBehavior: Flickable.StopAtBounds

			Column {
				id: buttonsCol
				width: flick.width
				spacing: 10
				Repeater {
					model: screen.menu.items
					MenuButton {
						width: buttonsCol.width
						text: modelData.label
						accent: modelData.action === "cmd_stop" ? Theme.danger : Theme.primary
						onClicked: Actions.activate(modelData, {
							backend: backend,
							openMenu: function (n) { screen.openMenu(n); },
							openInput: function (n, o) { screen.openInput(n, o); },
							close: function () { screen.close(); }
						})
					}
				}
			}
		}
	}
}
