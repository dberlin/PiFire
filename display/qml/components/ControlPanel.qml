import QtQuick
import QtQuick.Layouts
import ".."
import "../Menus.js" as Menus
import "../Actions.js" as Actions

RowLayout {
	id: panel
	property bool compact: false
	property string mode: "Stop"
	property bool recipe: false
	property bool recipePaused: false
	signal openMenu(string name)
	signal openInput(string name, string origin)
	spacing: panel.compact ? 12 : 16

	Repeater {
		model: Menus.controlPanelForMode(panel.mode, panel.recipe, panel.recipePaused)
		Rectangle {
			id: btn
			Layout.fillWidth: true
			Layout.fillHeight: true
			radius: panel.compact ? 12 : 16
			color: Theme.card
			border.width: 2
			border.color: (modelData.action === "cmd_stop" || modelData.action === "cmd_shutdown")
				? Theme.dangerColor
				: (modelData.active ? Theme.okColor : Theme.accentColor)
			opacity: enabled ? 1.0 : 0.4
			enabled: modelData.action !== "cmd_none"

			Text {
				anchors.centerIn: parent
				text: modelData.label
				font.family: Theme.sans
				font.pixelSize: panel.compact ? 20 : 25
				font.bold: true
				color: Theme.textColor
			}

			PressOverlay {
				pressed: mouse.pressed
				tint: (modelData.action === "cmd_stop" || modelData.action === "cmd_shutdown")
					? Theme.dangerColor
					: (modelData.active ? Theme.okColor : Theme.accentColor)
			}

			MouseArea {
				id: mouse
				anchors.fill: parent
				enabled: btn.enabled
				onClicked: Actions.activate(modelData, {
					backend: backend,
					openMenu: function (n) { panel.openMenu(n); },
					openInput: function (n, o) { panel.openInput(n, o); }
				})
			}
		}
	}
}
