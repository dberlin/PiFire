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

	// Screen rotation for physically-rotated panels. The Window stays at the
	// panel's native (landscape) resolution — the framebuffer is always native
	// — while all visual content lives inside `rotor`, which swaps to the
	// portrait-logical size and rotates to fill the native framebuffer at
	// 90/270. QML maps pointer events through the item transform, so touch
	// needs no manual coordinate correction.
	property int screenRot: (typeof screenRotation !== "undefined") ? screenRotation : 0
	property bool rotSwap: screenRot === 90 || screenRot === 270

	Item {
		id: rotor
		objectName: "rotor"
		width: root.rotSwap ? root.height : root.width
		height: root.rotSwap ? root.width : root.height
		anchors.centerIn: parent
		rotation: root.screenRot

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

		// Screen-sleep overlay. When the backend reports the display asleep, cover
		// everything with black; the first touch wakes it (and does not fall through
		// to whatever is underneath). The child process dims the backlight on
		// asleepChanged.
		Rectangle {
			id: sleepOverlay
			anchors.fill: parent
			color: "black"
			visible: backend && backend.asleep
			z: 1000
			MouseArea {
				anchors.fill: parent
				onPressed: {
					if (backend)
						backend.registerInteraction();
					if (root.dashItem)
						stack.pop(root.dashItem);
				}
			}
		}
	}

	Binding { target: Theme; property: "accent"; value: backend ? backend.accentTheme : "Ember" }

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

	function openInput(name, origin) {
		stack.push(name === "hold" ? holdComponent : notifyComponent,
		           name === "hold" ? {} : {origin: origin});
	}

	Component {
		id: dashComponent
		DashScreen {
			Component.onCompleted: root.dashItem = this
			onRequestMenu: (name) => root.openMenu(name === "" ? Menus.mainVariantForMode(backend.mode) : name)
			onRequestInput: (name, origin) => root.openInput(name, origin)
		}
	}

	Component {
		id: menuComponent
		MenuScreen {
			onClose: stack.pop(root.dashItem)
			onOpenMenu: (name) => root.openMenu(name)
			onOpenInput: (name, origin) => root.openInput(name, origin)
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
