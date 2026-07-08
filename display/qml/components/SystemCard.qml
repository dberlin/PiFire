import QtQuick
import ".."

// System card: fan / auger / igniter status rows, each with an animated
// icon, label, status text, and a status dot. Tapping a row toggles the
// matching backend output. Adapted from the working preview layout in
// tools/qt_dashboard_preview.qml, with the design's full-fidelity SVG icons
// (FanIcon / AugerIcon / IgniterIcon) in place of the preview's simplified
// placeholders. Sized by the caller (DashScreen, Task 15) — only height is
// implicit (content-driven); width is not bound.
Rectangle {
	id: card
	color: Theme.card
	radius: Theme.cardRadius
	border.color: Theme.cardBorder
	implicitHeight: col.implicitHeight + 32

	Column {
		id: col
		anchors.left: parent.left
		anchors.right: parent.right
		anchors.top: parent.top
		anchors.margins: 16
		spacing: 10

		Text {
			text: "SYSTEM"
			font.family: Theme.sans
			font.pixelSize: 13
			font.letterSpacing: 2.5
			color: Theme.label
		}

		// Fan row
		Rectangle {
			width: parent.width
			height: 66
			radius: 13
			color: Theme.inset
			border.color: backend.fanOn ? Qt.rgba(Theme.accentColor.r, Theme.accentColor.g, Theme.accentColor.b, 0.35) : Theme.cardBorder

			TapHandler { onTapped: backend.toggleFan() }

			Row {
				anchors.fill: parent
				anchors.leftMargin: 14
				anchors.rightMargin: 14
				spacing: 14

				Item {
					width: 64
					height: 46
					anchors.verticalCenter: parent.verticalCenter
					FanIcon {
						objectName: "sysFanIcon"
						anchors.centerIn: parent
						active: backend.fanOn
					}
				}
				Column {
					anchors.verticalCenter: parent.verticalCenter
					Text { text: "FAN"; font.family: Theme.sans; font.pixelSize: 17; color: Theme.rowLabel }
					Text {
						text: backend.fanOn ? "RUNNING" : "IDLE"
						font.family: Theme.sans
						font.pixelSize: 13
						font.letterSpacing: 2
						color: backend.fanOn ? Theme.accentColor : Theme.label
					}
				}
			}
			Rectangle {
				anchors.right: parent.right
				anchors.rightMargin: 14
				anchors.verticalCenter: parent.verticalCenter
				width: 9
				height: 9
				radius: 5
				color: backend.fanOn ? Theme.okColor : Theme.dotIdle
			}
		}

		// Auger row
		Rectangle {
			width: parent.width
			height: 66
			radius: 13
			color: Theme.inset
			border.color: backend.augerOn ? Qt.rgba(Theme.accentColor.r, Theme.accentColor.g, Theme.accentColor.b, 0.35) : Theme.cardBorder

			TapHandler { onTapped: backend.toggleAuger() }

			Row {
				anchors.fill: parent
				anchors.leftMargin: 14
				anchors.rightMargin: 14
				spacing: 14

				Item {
					width: 64
					height: 46
					anchors.verticalCenter: parent.verticalCenter
					AugerIcon {
						objectName: "sysAugerIcon"
						anchors.centerIn: parent
						active: backend.augerOn
					}
				}
				Column {
					anchors.verticalCenter: parent.verticalCenter
					Text { text: "AUGER"; font.family: Theme.sans; font.pixelSize: 17; color: Theme.rowLabel }
					Text {
						text: backend.augerOn ? "FEEDING" : "IDLE"
						font.family: Theme.sans
						font.pixelSize: 13
						font.letterSpacing: 2
						color: backend.augerOn ? Theme.accentColor : Theme.label
					}
				}
			}
			Rectangle {
				anchors.right: parent.right
				anchors.rightMargin: 14
				anchors.verticalCenter: parent.verticalCenter
				width: 9
				height: 9
				radius: 5
				color: backend.augerOn ? Theme.okColor : Theme.dotIdle
			}
		}

		// Igniter row
		Rectangle {
			width: parent.width
			height: 66
			radius: 13
			color: Theme.inset
			border.color: backend.igniterOn ? Qt.rgba(Theme.igniterColor.r, Theme.igniterColor.g, Theme.igniterColor.b, 0.4) : Theme.cardBorder

			TapHandler { onTapped: backend.toggleIgniter() }

			Row {
				anchors.fill: parent
				anchors.leftMargin: 14
				anchors.rightMargin: 14
				spacing: 14

				Item {
					width: 64
					height: 40
					anchors.verticalCenter: parent.verticalCenter
					IgniterIcon {
						objectName: "sysIgniterIcon"
						anchors.centerIn: parent
						active: backend.igniterOn
					}
				}
				Column {
					anchors.verticalCenter: parent.verticalCenter
					Text { text: "IGNITER"; font.family: Theme.sans; font.pixelSize: 17; color: Theme.rowLabel }
					Text {
						text: backend.igniterOn ? "HOT" : "OFF"
						font.family: Theme.sans
						font.pixelSize: 13
						font.letterSpacing: 2
						color: backend.igniterOn ? Theme.igniterColor : Theme.label
					}
				}
			}
			Rectangle {
				anchors.right: parent.right
				anchors.rightMargin: 14
				anchors.verticalCenter: parent.verticalCenter
				width: 9
				height: 9
				radius: 5
				color: backend.igniterOn ? Theme.igniterColor : Theme.dotIdle
			}
		}
	}
}
