import QtQuick
import QtQuick.Shapes
import ".."

// Primary gauge: 270° accent arc (track + value), animated setpoint marker,
// pulsing glow disc, and a center label/temp/SET/mode-pill column.
//
// Angle convention (preview-verified in tools/qt_dashboard_preview.qml): the
// arc's PathAngleArc and the setpoint marker both measure degrees clockwise
// from 3 o'clock with screen y-down — i.e. startAngle 135 is the lower-left
// start of the arc and sweepAngle 270 wraps clockwise back around to the
// lower-right. The marker is drawn as a radial line at that same angle
// rather than by rotating a 12-o'clock-anchored item (that lands ~90° off).
Item {
	id: g
	property bool compact: false
	property real value: 0
	// False when the pit probe has produced no reading at all. `value` is the
	// last real one whenever there is one.
	property bool hasValue: true
	// Set when `value` is a carried-over reading, e.g. "last data 47s ago".
	property string stale: ""
	property real setpoint: 0
	property real target: 0
	property real maxValue: 600
	property string label: ""
	property string units: "F"
	property string probeName: ""
	property color arcColor: Theme.accentColor
	property string modeLabel: ""
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

	TapHandler { id: tap; onTapped: g.tapped() }

	readonly property real _frac: Math.max(0, Math.min(1, value / Math.max(maxValue, 1)))
	readonly property real _spFrac: Math.max(0, Math.min(1, setpoint / Math.max(maxValue, 1)))
	readonly property real _radius: Math.min(width, height) / 2 - 16
	readonly property real _cx: width / 2
	readonly property real _cy: height / 2
	readonly property real _spAngleDeg: 135 + 270 * _spFrac
	readonly property real _spAngleRad: _spAngleDeg * Math.PI / 180

	// Glow: pulsing translucent disc behind the arc/track. Sized relative to
	// the gauge radius so it scales with the component instead of the design's
	// fixed 340px. MultiEffect blur is deliberately not used; this disc is the
	// accepted fallback.
	Rectangle {
		anchors.centerIn: parent
		width: (g._radius + 30) * 2
		height: width
		radius: width / 2
		color: Theme.glowColor
		opacity: 0.28
		SequentialAnimation on scale {
			running: g.value > 0
			loops: Animation.Infinite
			NumberAnimation { to: 1.06; duration: 1600; easing.type: Easing.InOutQuad }
			NumberAnimation { to: 1.0; duration: 1600; easing.type: Easing.InOutQuad }
		}
	}

	// Touch feedback: warm the dial face with the accent colour on press. A
	// circular disc (not the rectangular PressOverlay) matches the gauge shape.
	Rectangle {
		anchors.centerIn: parent
		width: g._radius * 2
		height: width
		radius: width / 2
		color: g.arcColor
		opacity: tap.pressed ? 0.18 : 0
		visible: opacity > 0
		Behavior on opacity { NumberAnimation { duration: 90 } }
	}

	Shape {
		anchors.fill: parent
		ShapePath {
			strokeColor: Theme.trackColor
			strokeWidth: 22
			fillColor: "transparent"
			capStyle: ShapePath.RoundCap
			PathAngleArc {
				centerX: g._cx
				centerY: g._cy
				radiusX: g._radius
				radiusY: g._radius
				startAngle: 135
				sweepAngle: 270
			}
		}
	}
	Shape {
		anchors.fill: parent
		ShapePath {
			// QML shape strokes can't take a gradient cleanly; a solid accent stroke
			// + the glow disc above is the accepted fidelity tradeoff.
			strokeColor: g.arcColor
			strokeWidth: 22
			fillColor: "transparent"
			capStyle: ShapePath.RoundCap
			PathAngleArc {
				centerX: g._cx
				centerY: g._cy
				radiusX: g._radius
				radiusY: g._radius
				startAngle: 135
				sweepAngle: 270 * g._frac
				Behavior on sweepAngle { NumberAnimation { duration: Theme.animMs; easing.type: Easing.OutCubic } }
			}
		}
	}

	// Setpoint marker: radial line at the setpoint angle, same convention as
	// the arc above. Hidden when there is no setpoint.
	Shape {
		id: spMarker
		objectName: "setpointMarker"
		anchors.fill: parent
		visible: g.setpoint > 0
		antialiasing: true
		ShapePath {
			strokeColor: Theme.setpoint
			strokeWidth: 4
			capStyle: ShapePath.RoundCap
			fillColor: "transparent"
			startX: g._cx + (g._radius - 13) * Math.cos(g._spAngleRad)
			startY: g._cy + (g._radius - 13) * Math.sin(g._spAngleRad)
			PathLine {
				x: g._cx + (g._radius + 9) * Math.cos(g._spAngleRad)
				y: g._cy + (g._radius + 9) * Math.sin(g._spAngleRad)
			}
		}
	}

	Repeater {
		model: g.healthModel
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
				if (model.displayName === g.probeName)
					g.health = healthSnapshot;
			}
			Component.onCompleted: syncHealth()
			onHealthSnapshotChanged: syncHealth()
			Component.onDestruction: {
				if (g.health && g.health.displayName === model.displayName)
					g.health = null;
			}
		}
	}

	Column {
		anchors.centerIn: parent
		spacing: 2
		Text {
			anchors.horizontalCenter: parent.horizontalCenter
			text: (g.probeName || g.label).toUpperCase()
			font.family: Theme.sans
			font.pixelSize: g.compact ? 12 : 14
			font.letterSpacing: 4
			color: Theme.label
		}
		Row {
			anchors.horizontalCenter: parent.horizontalCenter
			spacing: 4
			Text {
				objectName: "primaryTemperature"
				text: g.hasValue ? Math.round(g.value) : "—"
				font.family: Theme.condensed
				font.pixelSize: g.compact ? 66 : 84
				font.bold: true
				color: Theme.textColor
			}
			Text {
				text: "°" + g.units
				font.family: Theme.condensed
				font.pixelSize: g.compact ? 24 : 30
				color: Theme.dim
				anchors.bottom: parent.bottom
				anchors.bottomMargin: 10
			}
		}
		// Health and stale transport share one reserved status line.
		Text {
			objectName: "primaryHealthStatus"
			anchors.horizontalCenter: parent.horizontalCenter
			visible: g.statusText !== ""
			text: g.statusText
			font.family: Theme.sans
			font.pixelSize: g.compact ? 13 : 15
			font.bold: true
			color: g.statusColor
		}
		Text {
			anchors.horizontalCenter: parent.horizontalCenter
			visible: g.setpoint > 0
			text: "SET " + Math.round(g.setpoint) + "°"
			font.family: Theme.sans
			font.pixelSize: g.compact ? 16 : 20
			font.letterSpacing: 1
			color: Theme.setpoint
		}
		Rectangle {
			anchors.horizontalCenter: parent.horizontalCenter
			visible: g.modeLabel.length > 0
			height: g.compact ? 28 : 34
			width: pillText.width + 40
			radius: g.compact ? 14 : 17
			color: Qt.rgba(g.arcColor.r, g.arcColor.g, g.arcColor.b, 0.14)
			border.color: Qt.rgba(g.arcColor.r, g.arcColor.g, g.arcColor.b, 0.55)
			border.width: 1.5
			Text {
				id: pillText
				anchors.centerIn: parent
				text: g.modeLabel.toUpperCase()
				font.family: Theme.sans
				font.pixelSize: g.compact ? 14 : 17
				font.bold: true
				font.letterSpacing: 3
				color: g.arcColor
			}
		}
	}
}
