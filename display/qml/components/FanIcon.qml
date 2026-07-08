import QtQuick
import QtQuick.Shapes
import ".."

// Three-blade fan icon. Ported from the design's inline
// <svg viewBox="0 0 100 100"> fan blades (paths pre-scaled by 0.46 to fit a
// compact 46x46 icon, matching the working preview in
// tools/qt_dashboard_preview.qml) plus the design's small center hub circle.
// Spins via RotationAnimation (pf-spin) while active && animate.
Item {
	id: root
	implicitWidth: 46
	implicitHeight: 46
	property bool active: false
	property bool animate: true

	readonly property color bladeColor: root.active ? Theme.accentColor : Theme.iconIdle

	Shape {
		id: blades
		anchors.centerIn: parent
		width: 46
		height: 46
		transformOrigin: Item.Center

		RotationAnimation on rotation {
			running: root.active && root.animate
			from: 0
			to: 360
			duration: 850
			loops: Animation.Infinite
		}

		ShapePath {
			fillColor: root.bladeColor
			strokeColor: "transparent"
			PathSvg { path: "M23 23 Q 13 14 16 5 Q 23 2 23 23 Z" }
			PathSvg { path: "M23 23 Q 36 19 42 27 Q 40 35 23 23 Z" }
			PathSvg { path: "M23 23 Q 20 36 11 39 Q 5 33 23 23 Z" }
		}
	}

	// center hub, from the design's <circle cx="50" cy="50" r="8" .../> (scaled 0.46)
	Rectangle {
		anchors.centerIn: parent
		width: 8
		height: 8
		radius: 4
		color: Theme.inset
		border.color: root.bladeColor
		border.width: 1.5
	}
}
