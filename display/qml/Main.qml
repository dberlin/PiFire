import QtQuick
import QtQuick.Window
import QtQuick.Controls
import "."
import "screens"

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

	Component {
		id: dashComponent
		DashScreen {
			Component.onCompleted: root.dashItem = this
			onOpenMenu: stack.push(menuComponent)
		}
	}

	// Replaced by MenuScreen in Task 7.
	Component {
		id: menuComponent
		Rectangle { color: Theme.background }
	}
}
