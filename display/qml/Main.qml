import QtQuick
import QtQuick.Window
import QtQuick.Controls
import "."
import "screens"
import "Menus.js" as Menus

Window {
	id: root
	width: screenWidth
	height: screenHeight
	visible: true
	color: Theme.background
	title: "PiFire"

	StackView {
		id: stack
		anchors.fill: parent
		initialItem: splashComponent
	}

	Component {
		id: splashComponent
		Item {
			Image {
				anchors.centerIn: parent
				source: splashImage ? "file:" + splashImage : ""
				fillMode: Image.PreserveAspectFit
			}
			Timer {
				interval: splashDelay
				running: true
				repeat: false
				onTriggered: stack.replace(dashComponent)
			}
		}
	}

	property Item dashItem

	function openMenu(name) {
		if (name === "qrcode")
			stack.push(qrCodeComponent);
		else
			stack.push(menuComponent, {menuName: name});
	}

	function openInput(name) {
		stack.push(name === "hold" ? holdComponent : notifyComponent,
		           name === "hold" ? {} : {origin: name});
	}

	Component {
		id: dashComponent
		DashScreen {
			Component.onCompleted: root.dashItem = this
			onOpenMenu: root.openMenu(Menus.mainVariantForMode(backend.mode))
		}
	}

	Component {
		id: menuComponent
		MenuScreen {
			onClose: stack.pop(root.dashItem)
			onOpenMenu: (name) => root.openMenu(name)
			onOpenInput: (name) => root.openInput(name)
		}
	}

	Component {
		id: qrCodeComponent
		QrCodeScreen { onClose: stack.pop(root.dashItem) }
	}

	Component {
		id: holdComponent
		HoldInput { onClose: stack.pop(root.dashItem) }
	}
	Component {
		id: notifyComponent
		NotifyInput { onClose: stack.pop(root.dashItem) }
	}
}
