pragma Singleton
import QtQuick

QtObject {
	property FontLoader _barlow: FontLoader { source: "../../static/font/Barlow-SemiBold.ttf" }
	property FontLoader _barlowSemi: FontLoader { source: "../../static/font/BarlowSemiCondensed-Bold.ttf" }
	readonly property string sans: _barlow.status === FontLoader.Ready ? _barlow.name : "sans-serif"
	readonly property string condensed: _barlowSemi.status === FontLoader.Ready ? _barlowSemi.name : sans
}
