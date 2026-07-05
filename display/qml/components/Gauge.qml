import QtQuick
import QtQuick.Shapes
import ".."

Item {
	id: g
	property real value: 0
	property real setpoint: 0
	property real target: 0
	property real maxValue: 600
	property string label: ""
	property string units: "F"
	property string probeName: ""
	property color arcColor: Theme.primary
	signal tapped()

	TapHandler { onTapped: g.tapped() }

	readonly property real _frac: Math.max(0, Math.min(1, value / Math.max(maxValue, 1)))
	readonly property real _radius: Math.min(width, height) / 2 - 16

	Shape {
		anchors.fill: parent
		ShapePath {
			strokeColor: Theme.surface
			strokeWidth: 22
			fillColor: "transparent"
			capStyle: ShapePath.RoundCap
			PathAngleArc {
				centerX: g.width / 2
				centerY: g.height / 2
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
			strokeColor: g.arcColor
			strokeWidth: 22
			fillColor: "transparent"
			capStyle: ShapePath.RoundCap
			PathAngleArc {
				centerX: g.width / 2
				centerY: g.height / 2
				radiusX: g._radius
				radiusY: g._radius
				startAngle: 135
				sweepAngle: 270 * g._frac
				Behavior on sweepAngle { NumberAnimation { duration: Theme.animMs } }
			}
		}
	}
	Column {
		anchors.centerIn: parent
		spacing: 4
		Text {
			anchors.horizontalCenter: parent.horizontalCenter
			text: g.label
			color: Theme.subtext
			font.pixelSize: 28
		}
		Text {
			anchors.horizontalCenter: parent.horizontalCenter
			text: Math.round(g.value) + "°" + g.units
			color: Theme.text
			font.pixelSize: 84
			font.bold: true
		}
		Text {
			anchors.horizontalCenter: parent.horizontalCenter
			visible: g.setpoint > 0
			text: "Set " + Math.round(g.setpoint) + "°"
			color: Theme.primary
			font.pixelSize: 30
		}
		Text {
			anchors.horizontalCenter: parent.horizontalCenter
			visible: g.target > 0
			text: "→ " + Math.round(g.target) + "°"
			color: Theme.notify
			font.pixelSize: 26
		}
	}
}
