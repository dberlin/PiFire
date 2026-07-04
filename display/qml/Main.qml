import QtQuick
import QtQuick.Window
import QtQuick.Controls
import "."

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

	// Replaced by DashScreen in Task 6.
	Component {
		id: dashComponent
		Rectangle { color: Theme.background }
	}
}
