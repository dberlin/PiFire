import QtQuick
import ".."

Rectangle {
	id: banner
	objectName: "probeHealthBanner"

	property var summary: ({})
	property bool compact: false
	readonly property var highest: summary && summary.highest ? summary.highest : null
	readonly property bool active: highest !== null && highest.state === "confirmed"
	readonly property bool dismissible: false
	readonly property string summaryText: {
		if (!active)
			return "";
		var prefix = highest.freshnessQualifier ? highest.freshnessQualifier + ": " : "";
		var text = prefix + highest.displayName + ": " + highest.headline;
		if (highest.impactCopy)
			text += ". " + highest.impactCopy;
		if (summary.additionalCopy)
			text += " " + summary.additionalCopy;
		return text;
	}

	signal clicked()

	visible: active
	height: compact ? 52 : 58
	radius: Theme.cardRadius
	color: Theme.dangerSurface
	border.color: activeFocus ? Theme.focusRing : Theme.danger
	border.width: activeFocus ? 3 : 2
	focus: false
	activeFocusOnTab: active

	Accessible.role: Accessible.Button
	Accessible.name: "Thermocouple health alert"
	Accessible.description: summaryText + ". Open details."
	Accessible.onPressAction: banner.clicked()

	TapHandler { onTapped: banner.clicked() }
	Keys.onReturnPressed: banner.clicked()
	Keys.onEnterPressed: banner.clicked()
	Keys.onSpacePressed: banner.clicked()

	Row {
		anchors.fill: parent
		anchors.leftMargin: banner.compact ? 14 : 18
		anchors.rightMargin: banner.compact ? 14 : 18
		spacing: banner.compact ? 10 : 14

		Text {
			anchors.verticalCenter: parent.verticalCenter
			text: "!"
			font.family: Theme.sans
			font.pixelSize: banner.compact ? 22 : 26
			font.bold: true
			color: Theme.danger
		}

		Text {
			width: parent.width - detailsCue.width - (banner.compact ? 48 : 60)
			anchors.verticalCenter: parent.verticalCenter
			text: banner.summaryText
			font.family: Theme.sans
			font.pixelSize: banner.compact ? 13 : 15
			font.bold: true
			color: Theme.text
			wrapMode: Text.Wrap
			maximumLineCount: 2
			elide: Text.ElideRight
		}

		Text {
			id: detailsCue
			anchors.verticalCenter: parent.verticalCenter
			text: "DETAILS  ›"
			font.family: Theme.sans
			font.pixelSize: banner.compact ? 11 : 13
			font.bold: true
			font.letterSpacing: 1
			color: Theme.danger
		}
	}
}
