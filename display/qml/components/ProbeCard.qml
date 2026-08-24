import QtQuick
import ".."

// Food-probe card: name, target string (→ N° / AMBIENT), big temp, progress
// bar. Sized by the caller's Layout (DashScreen's food-probe Repeater, Task
// 15) — this component does not bind its own width/height. Adapted from the
// preview-verified left-column probe card in tools/qt_dashboard_preview.qml.
Rectangle {
	id: card
	property bool compact: false
	property string name: ""
	property real temp: 0
	// False when the probe has produced no reading at all, so there is no
	// number to show. `temp` is the last real reading whenever there is one,
	// which is why it stays a plain real: the model resolves the absence
	// rather than passing a null down and letting the assignment fail.
	property bool hasTemp: true
	// Set when `temp` is a carried-over reading, e.g. "last data 47s ago".
	property string stale: ""
	property real target: 0
	property real maxTemp: 300
	property string units: "F"
	property var healthModel: null
	property var health: null
	readonly property bool healthIssue: health !== null
		&& (health.state === "suspected" || health.state === "confirmed")
	readonly property string statusText: healthIssue
		? ((health.freshnessQualifier ? health.freshnessQualifier + ": " : "") + health.headline)
		: stale
	readonly property color statusColor: healthIssue
		? (health.severity === "danger" ? Theme.danger : Theme.warn)
		: Theme.warn
	signal tapped()

	readonly property bool done: hasTemp && target > 0 && temp >= target - 1

	color: Theme.card
	radius: Theme.cardRadius
	border.color: Theme.cardBorder

	TapHandler { id: tap; onTapped: card.tapped() }
	PressOverlay { pressed: tap.pressed }

	Repeater {
		model: card.healthModel
		delegate: Item {
			width: 0
			height: 0
			visible: false
			readonly property var healthSnapshot: ({
				"displayName": model.displayName,
				"state": model.state,
				"severity": model.severity,
				"headline": model.headline,
				"freshnessQualifier": model.freshnessQualifier
			})
			function syncHealth() {
				if (model.displayName === card.name)
					card.health = healthSnapshot;
			}
			Component.onCompleted: syncHealth()
			onHealthSnapshotChanged: syncHealth()
			Component.onDestruction: {
				if (card.health && card.health.displayName === model.displayName)
					card.health = null;
			}
		}
	}

	Column {
		anchors.verticalCenter: parent.verticalCenter
		anchors.left: parent.left
		anchors.right: parent.right
		anchors.leftMargin: 18
		anchors.rightMargin: 18
		spacing: 4

		// header: name (left) + target (right) via anchors — no width feedback
		Item {
			width: parent.width
			height: nameText.implicitHeight
			Text {
				id: nameText
				anchors.left: parent.left
				anchors.verticalCenter: parent.verticalCenter
				text: card.name.toUpperCase()
				font.family: Theme.sans
				font.pixelSize: card.compact ? 13 : 15
				font.letterSpacing: 1.5
				color: Theme.probeLabel
			}
			Text {
				anchors.right: parent.right
				anchors.verticalCenter: parent.verticalCenter
				text: card.target > 0 ? "→ " + card.target + "°" : "AMBIENT"
				font.family: Theme.sans
				font.pixelSize: card.compact ? 13 : 15
				color: card.target > 0 ? (card.done ? Theme.okColor : Theme.cookingColor) : Theme.label
			}
		}

		Row {
			spacing: 2
			Text {
				objectName: "foodTemperature-" + card.name
				text: card.hasTemp ? Math.round(card.temp) : "—"
				font.family: Theme.condensed
				font.pixelSize: card.compact ? 52 : 66
				font.bold: true
				color: Theme.textColor
			}
			Text {
				text: "°" + card.units
				font.family: Theme.condensed
				font.pixelSize: card.compact ? 20 : 26
				color: Theme.dim
				anchors.bottom: parent.bottom
				anchors.bottomMargin: 8
			}
		}

		// Health and stale transport share one reserved status line.
		Text {
			objectName: "foodHealthStatus-" + card.name
			visible: card.statusText !== ""
			text: card.statusText
			font.family: Theme.sans
			font.pixelSize: card.compact ? 11 : 13
			font.bold: true
			color: card.statusColor
		}

		Rectangle {
			width: parent.width
			height: 6
			radius: 3
			color: Qt.rgba(1, 1, 1, 0.11)
			Rectangle {
				height: parent.height
				radius: 3
				width: parent.width * (card.hasTemp && card.target > 0 ? Math.max(0.02, Math.min(1, card.temp / card.target)) : 0)
				color: card.done ? Theme.okColor : Theme.accentColor
				Behavior on width { NumberAnimation { duration: 900; easing.type: Easing.OutCubic } }
			}
		}
	}
}
