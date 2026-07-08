import QtQuick
import QtQuick.Shapes
import ".."

// Igniter flame icon: a coil-shaped flame path with a flicker opacity
// animation, plus three rising heat-wave strokes above it (animated together
// as a single cluster, matching the design where one shared animation drives
// the whole <g> of wisps). Ported from the design's inline
// <svg viewBox="0 0 100 60"> (coordinates scaled by 0.6); keyframes
// pf-flicker / pf-heat.
Item {
	id: root
	implicitWidth: 60
	implicitHeight: 36
	property bool active: false
	property bool animate: true

	readonly property color flameColor: root.active ? Theme.igniterColor : Theme.iconIdle

	// rising heat waves — one shared animation moves/scales/fades the cluster
	Item {
		id: heatGroup
		anchors.fill: parent
		visible: root.active

		property real dy: 0
		property real waveScale: 1

		transform: [
			Translate { y: heatGroup.dy },
			Scale { xScale: heatGroup.waveScale; origin.x: 30; origin.y: 18 }
		]

		Shape {
			anchors.fill: parent
			ShapePath {
				strokeColor: "#ff9f43"
				strokeWidth: 1.8
				fillColor: "transparent"
				capStyle: ShapePath.RoundCap
				PathSvg { path: "M18 24 Q 20.4 15.6 22.8 24" }
			}
			ShapePath {
				strokeColor: "#ff7a1a"
				strokeWidth: 1.8
				fillColor: "transparent"
				capStyle: ShapePath.RoundCap
				PathSvg { path: "M30 25.2 Q 32.4 15.6 34.8 25.2" }
			}
			ShapePath {
				strokeColor: "#ffb066"
				strokeWidth: 1.8
				fillColor: "transparent"
				capStyle: ShapePath.RoundCap
				PathSvg { path: "M40.8 24 Q 43.2 16.8 45.6 24" }
			}
		}

		SequentialAnimation {
			running: root.active && root.animate
			loops: Animation.Infinite

			ParallelAnimation {
				NumberAnimation { target: heatGroup; property: "dy"; from: 1.2; to: -9.6; duration: 1200; easing.type: Easing.Linear }
				NumberAnimation { target: heatGroup; property: "waveScale"; from: 1; to: 1.3; duration: 1200; easing.type: Easing.Linear }
				SequentialAnimation {
					NumberAnimation { target: heatGroup; property: "opacity"; from: 0; to: 0.7; duration: 360 }
					NumberAnimation { target: heatGroup; property: "opacity"; to: 0; duration: 840 }
				}
			}
		}
	}

	// flame coil
	Shape {
		id: coil
		anchors.fill: parent

		ShapePath {
			strokeColor: root.flameColor
			strokeWidth: 3.6
			fillColor: "transparent"
			capStyle: ShapePath.RoundCap
			PathSvg { path: "M4.8 24 C 9.6 8.4 18 8.4 20.4 24 C 22.8 33.6 30 33.6 32.4 24 C 34.8 8.4 43.2 8.4 45.6 24 C 48 33.6 55.2 33.6 55.2 24" }
		}

		SequentialAnimation {
			running: root.active && root.animate
			loops: Animation.Infinite
			NumberAnimation { target: coil; property: "opacity"; from: 1.0; to: 0.62; duration: 720 }
			NumberAnimation { target: coil; property: "opacity"; to: 0.92; duration: 270 }
			NumberAnimation { target: coil; property: "opacity"; to: 0.55; duration: 306 }
			NumberAnimation { target: coil; property: "opacity"; to: 0.85; duration: 234 }
			NumberAnimation { target: coil; property: "opacity"; to: 1.0; duration: 270 }
		}
	}
}
