import QtQuick
import QtQuick.Controls
import ".."
import "../components"

Item {
	id: screen
	objectName: "probeHealthScreen"
	signal close()

	Keys.onEscapePressed: screen.close()

	Rectangle {
		anchors.fill: parent
		color: Qt.rgba(0, 0, 0, 0.72)
		MouseArea { anchors.fill: parent }
	}

	Rectangle {
		id: card
		objectName: "probeHealthDetailsCard"
		anchors.horizontalCenter: parent.horizontalCenter
		anchors.top: parent.top
		anchors.topMargin: Math.max(122, Math.min(128, parent.height * 0.18))
		width: Math.min(parent.width - 24, 920)
		height: Math.min(parent.height - anchors.topMargin - 12, 660)
		radius: Theme.cardRadius
		color: Theme.background
		border.color: Theme.danger
		border.width: 2
		clip: true

		Text {
			id: title
			anchors.left: parent.left
			anchors.top: parent.top
			anchors.leftMargin: 22
			anchors.topMargin: 18
			text: "THERMOCOUPLE HEALTH"
			font.family: Theme.sans
			font.pixelSize: parent.width < 700 ? 19 : 25
			font.bold: true
			font.letterSpacing: 1.5
			color: Theme.text
		}

		Button {
			id: closeButton
			objectName: "probeHealthCloseButton"
			anchors.right: parent.right
			anchors.top: parent.top
			anchors.rightMargin: 12
			anchors.topMargin: 8
			width: 48
			height: 48
			activeFocusOnTab: true
			text: "×"
			font.pixelSize: 28
			Accessible.name: "Close thermocouple health details"
			Accessible.description: "Return to the previous PiFire screen."
			onClicked: screen.close()
			background: Rectangle {
				radius: 12
				color: closeButton.down ? Theme.dangerSurface : Theme.inset
				border.color: closeButton.activeFocus ? Theme.focusRing : Theme.cardBorder
				border.width: closeButton.activeFocus ? 3 : 1
			}
			contentItem: Text {
				text: closeButton.text
				font: closeButton.font
				color: Theme.text
				horizontalAlignment: Text.AlignHCenter
				verticalAlignment: Text.AlignVCenter
			}
		}

		Flickable {
			id: detailsFlick
			objectName: "probeHealthDetailsFlick"
			anchors.left: parent.left
			anchors.right: parent.right
			anchors.top: title.bottom
			anchors.bottom: parent.bottom
			anchors.leftMargin: 16
			anchors.rightMargin: 16
			anchors.topMargin: 14
			anchors.bottomMargin: 14
			contentWidth: width
			contentHeight: detailsColumn.implicitHeight
			clip: true
			boundsBehavior: Flickable.StopAtBounds
			flickableDirection: Flickable.VerticalFlick

			Column {
				id: detailsColumn
				width: detailsFlick.width
				spacing: 12

				Repeater {
					model: backend.probeHealth
					delegate: Rectangle {
						id: detailCard
						width: detailsColumn.width
						height: detailContent.implicitHeight + 28
						radius: 14
						color: model.state === "confirmed" ? Theme.dangerSurface
							: model.state === "suspected" ? Theme.warningSurface : Theme.inset
						border.color: model.state === "confirmed" ? Theme.danger
							: model.state === "suspected" ? Theme.warn : Theme.cardBorder
						border.width: model.state === "confirmed" || model.state === "suspected" ? 2 : 1

						function listText(values) {
							if (!values || values.length === 0)
								return "None";
							return Array.prototype.join.call(values, ", ");
						}

						Column {
							id: detailContent
							anchors.left: parent.left
							anchors.right: parent.right
							anchors.top: parent.top
							anchors.margins: 14
							spacing: 6

							Text {
								objectName: "healthDetailName-" + model.displayName
								width: parent.width
								text: model.displayName + " · " + model.role
								font.family: Theme.sans
								font.pixelSize: card.width < 700 ? 17 : 20
								font.bold: true
								color: Theme.text
								elide: Text.ElideRight
							}

							Text {
								width: parent.width
								text: {
									var qualifier = model.freshnessQualifier ? model.freshnessQualifier + ": " : "";
									if (model.state === "healthy")
										return qualifier + "Healthy";
									if (model.state === "unmonitored")
										return qualifier + "Not monitored";
									return qualifier + model.headline;
								}
								font.family: Theme.sans
								font.pixelSize: card.width < 700 ? 14 : 16
								font.bold: true
								color: model.severity === "danger" ? Theme.danger
									: model.severity === "warning" ? Theme.warn : Theme.probeLabel
								wrapMode: Text.Wrap
							}

							Text {
								visible: text !== ""
								width: parent.width
								text: model.impactCopy || ""
								font.family: Theme.sans
								font.pixelSize: card.width < 700 ? 13 : 15
								color: Theme.text
								wrapMode: Text.Wrap
							}

							Text {
								visible: text !== ""
								width: parent.width
								text: model.causeCopy || ""
								font.family: Theme.sans
								font.pixelSize: card.width < 700 ? 12 : 14
								color: Theme.probeLabel
								wrapMode: Text.Wrap
							}

							Text {
								objectName: "healthDetailTechnical-" + model.displayName
								width: parent.width
								text: "Source: " + model.sourceCopy
									+ "  ·  Policy: " + model.policy
									+ "  ·  Outcome: " + model.outcome
									+ "\nFaults: " + detailCard.listText(model.faults)
									+ "  ·  Evidence: " + detailCard.listText(model.evidence)
								font.family: Theme.sans
								font.pixelSize: card.width < 700 ? 11 : 13
								color: Theme.dim
								wrapMode: Text.Wrap
							}
						}
					}
				}
			}
		}
	}

	StackView.onActivated: closeButton.forceActiveFocus()
}
