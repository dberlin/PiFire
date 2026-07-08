pragma Singleton
import QtQuick

QtObject {
	// Selected accent — bound live from backend.accentTheme in Main.qml.
	property string accent: "Ember"

	// Base palette (design-verbatim)
	readonly property color page:        "#0c0a09"
	readonly property color card:        "#1a1611"
	readonly property color inset:       "#14100c"
	readonly property color cardBorder:  Qt.rgba(1, 1, 1, 0.05)
	readonly property color textColor:   "#f4ede2"
	readonly property color dim:         "#8a7f70"
	readonly property color label:       "#7d7264"
	readonly property color probeLabel:  "#b7ac9c"
	readonly property color setpoint:    "#6cc8ff"
	readonly property color okColor:     "#5ec96f"
	readonly property color warn:        "#ffb020"
	readonly property color dangerColor: "#ff5a4d"
	readonly property color trackColor: "#2a241d"
	readonly property color cookingColor: "#ffd23f"

	// SystemCard tokens (design-verbatim, fixed regardless of accent)
	readonly property color igniterColor: "#ff7a1a"
	readonly property color iconIdle:     "#57514a"
	readonly property color dotIdle:      "#4a443c"
	readonly property color rowLabel:     "#cfc6b8"

	// Accent-derived tokens
	readonly property color accentColor: accent === "Ice" ? "#3cc7d0" : accent === "Crimson" ? "#ff6a5a" : "#ff8a2b"
	readonly property color glowColor:   accent === "Ice" ? "#2ec5d3" : accent === "Crimson" ? "#ff5a4d" : "#ff7a1a"
	readonly property color arcStop0:    accent === "Ice" ? "#1f9fb8" : accent === "Crimson" ? "#e11d48" : "#ff5e1a"
	readonly property color arcStop1:    accent === "Ice" ? "#35c7d0" : accent === "Crimson" ? "#ff5a4d" : "#ff8a2b"
	readonly property color arcStop2:    accent === "Ice" ? "#7ef0d2" : accent === "Crimson" ? "#ff9f43" : "#ffc24b"

	// Sizing
	readonly property int cardRadius: 18
	readonly property int pillRadius: 999
	readonly property int animMs: 250

	// Fonts (from the Fonts singleton)
	readonly property string sans: Fonts.sans
	readonly property string condensed: Fonts.condensed

	// Back-compat aliases for existing menu/input components:
	readonly property color background: page
	readonly property color surface: card
	readonly property color primary: setpoint
	readonly property color notify: "#ffff00"
	readonly property color text: textColor
	readonly property color subtext: dim
	readonly property color danger: dangerColor
	readonly property color ok: okColor
	readonly property int radius: cardRadius
	readonly property string fontFamily: sans
}
