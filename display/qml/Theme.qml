pragma Singleton
import QtQuick

QtObject {
	readonly property color background: "#141414"
	readonly property color surface: "#1e1e1e"
	readonly property color primary: "#00c8ff"
	readonly property color notify: "#ffff00"
	readonly property color text: "#ffffff"
	readonly property color subtext: "#9a9a9a"
	readonly property color danger: "#ff4444"
	readonly property color ok: "#44cc66"
	readonly property int radius: 16
	readonly property int animMs: 250
	readonly property string fontFamily: "Trebuchet MS"
}
