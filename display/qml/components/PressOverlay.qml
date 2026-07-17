import QtQuick
import ".."

// Reusable touch-feedback overlay. Drop it as a child of any tappable
// Rectangle and bind `pressed` to that element's TapHandler/MouseArea
// `pressed` property (or a Button's `down`). On press it warms the surface
// with the accent colour, so the button appears to glow like an ember —
// which fits the dark, warm palette (the cards are near-black #1a1611, so a
// plain darken reads as nothing and a white flash fights the theme). The
// tint follows the live accent, so it turns orange/cyan/red with the theme.
//
// `tint` defaults to the accent; danger controls can override it to the
// danger colour. Purely visual: it declares no pointer handler, so it never
// steals touches from the element's own handler.
Rectangle {
	id: fx
	property bool pressed: false
	property color tint: Theme.accentColor
	// Render above the element's content so the press reads even on cards whose
	// content fills the surface (e.g. the hopper level bar). A plain Rectangle
	// accepts no pointer events, so sitting on top never steals the tap.
	z: 100
	anchors.fill: parent
	radius: parent.radius
	color: fx.tint
	opacity: pressed ? 0.22 : 0
	visible: opacity > 0
	Behavior on opacity { NumberAnimation { duration: 90 } }
}
