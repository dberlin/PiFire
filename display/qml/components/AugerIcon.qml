import QtQuick
import QtQuick.Shapes
import ".."

// Auger feed icon: an endlessly-scrolling diagonal-stroke "screw" clipped
// inside a rounded track, plus two pellets dropping out of it. Ported from
// the design's inline <svg viewBox="0 0 120 60"> (coordinates scaled by 0.5,
// and re-based to the track's own local origin, to fit a compact row icon);
// keyframes pf-augerFeed / pf-pellet.
//
// QtQuick.Item.clip only clips to an axis-aligned rectangle (no rounded
// corners), so the scrolling strokes are clipped square and the rounded
// track outline is drawn on top to visually round the corners off — at this
// icon's size (~50x13px track) the difference is not perceptible.
Item {
	id: root
	implicitWidth: 60
	implicitHeight: 40
	property bool active: false
	property bool animate: true

	readonly property color screwColor: root.active ? Theme.accentColor : Theme.iconIdle
	readonly property real trackX: 5
	readonly property real trackY: 9
	readonly property real trackW: 50
	readonly property real trackH: 13
	readonly property real trackR: 6.5

	// clipped, scrolling screw strokes
	Item {
		x: root.trackX
		y: root.trackY
		width: root.trackW
		height: root.trackH
		clip: true

		Rectangle { anchors.fill: parent; color: "#0e0b08" }

		Shape {
			id: feed
			x: 0
			y: 0
			width: root.trackW + 100
			height: root.trackH

			ShapePath {
				strokeColor: root.screwColor
				strokeWidth: 3
				fillColor: "transparent"
				capStyle: ShapePath.RoundCap
				PathSvg { path: "M-25 14 L-12 -2 M-17 14 L-4 -2 M-9 14 L4 -2 M-1 14 L12 -2 M7 14 L20 -2 M15 14 L28 -2 M23 14 L36 -2 M31 14 L44 -2 M39 14 L52 -2 M47 14 L60 -2 M55 14 L68 -2 M63 14 L76 -2" }
			}

			NumberAnimation on x {
				running: root.active && root.animate
				from: 0
				to: -8
				duration: 650
				loops: Animation.Infinite
			}
		}
	}

	// rounded track outline, drawn on top of the clipped strokes
	Rectangle {
		x: root.trackX
		y: root.trackY
		width: root.trackW
		height: root.trackH
		radius: root.trackR
		color: "transparent"
		border.color: root.screwColor
		border.width: 1.5
	}

	// falling pellets (scaled from design cx=30/40 cy=10 r=3.4)
	Repeater {
		model: 2
		delegate: Rectangle {
			id: pellet
			required property int index
			width: 3.4
			height: 3.4
			radius: 1.7
			color: root.screwColor
			visible: root.active
			x: root.trackX + (index === 0 ? 10 : 15) - width / 2
			y: 5 - height / 2
			opacity: 0
			property real dy: 0
			transform: Translate { y: pellet.dy }

			SequentialAnimation {
				running: root.active && root.animate
				loops: Animation.Infinite

				PauseAnimation { duration: pellet.index * 700 }

				ParallelAnimation {
					NumberAnimation { target: pellet; property: "dy"; from: -7; to: 5.5; duration: 1400; easing.type: Easing.Linear }
					SequentialAnimation {
						NumberAnimation { target: pellet; property: "opacity"; from: 0; to: 1; duration: 350 }
						PauseAnimation { duration: 770 }
						NumberAnimation { target: pellet; property: "opacity"; to: 0; duration: 280 }
					}
				}
			}
		}
	}
}
