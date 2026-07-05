import QtQuick
import QtQuick.Layouts
import ".."
import "../Menus.js" as Menus
import "../Actions.js" as Actions

RowLayout {
	id: panel
	property string mode: "Stop"
	property bool recipe: false
	property bool recipePaused: false
	signal openMenu(string name)
	signal openInput(string name, string origin)
	spacing: 16

	Repeater {
		model: Menus.controlPanelForMode(panel.mode, panel.recipe, panel.recipePaused)
		MenuButton {
			Layout.fillWidth: true
			text: modelData.label
			enabled: modelData.action !== "cmd_none"
			accent: (modelData.action === "cmd_stop" || modelData.action === "cmd_shutdown")
				? Theme.danger
				: (modelData.active ? Theme.ok : Theme.primary)
			onClicked: Actions.activate(modelData, {
				backend: backend,
				openMenu: function (n) { panel.openMenu(n); },
				openInput: function (n, o) { panel.openInput(n, o); }
			})
		}
	}
}
