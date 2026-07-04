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

	// Button/encoder parity: hardware GPIO handlers call backend.navUp/navDown/
	// navEnter directly; this maps those (and desktop arrow keys) to QML focus
	// traversal and activation. A plain Item does not consume touch events, so
	// this overlay leaves the primary touch path untouched.
	Item {
		id: keyNav
		anchors.fill: parent
		focus: true
		Keys.onUpPressed: backend.navUp()
		Keys.onDownPressed: backend.navDown()
		Keys.onReturnPressed: backend.navEnter()
		Keys.onEnterPressed: backend.navEnter()
	}

	Connections {
		target: backend
		function onNavEvent(dir) {
			var f = root.activeFocusItem;
			if (dir === "ENTER") {
				if (f && f.clicked)
					f.clicked();
			} else if (f) {
				var next = f.nextItemInFocusChain(dir === "DOWN");
				if (next)
					next.forceActiveFocus();
			}
		}
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
