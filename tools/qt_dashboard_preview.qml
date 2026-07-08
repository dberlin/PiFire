// PiFire Dashboard — standalone FPS / fidelity preview.
//
// Self-contained: no Redis, no control stack. A built-in simulator drives fake
// live data; all motion is real QML animation so the on-screen FPS counter
// reflects the true rendering cost of the redesign.
//
// Controls:  click / M = cycle mode · A = cycle accent · P = toggle food probes ·
//            L = toggle lid-open alert · F = toggle all animation (isolate layout cost)
//
// Run:  python tools/qt_dashboard_preview.py
import QtQuick
import QtQuick.Window
import QtQuick.Shapes
import QtQuick.Layouts

Window {
    id: win
    // viewW/viewH are set by the runner (default 1280x720). Set larger to see the
    // design scale uniformly onto a bigger 16:9 screen.
    width: typeof viewW !== "undefined" ? viewW : 1280
    height: typeof viewH !== "undefined" ? viewH : 720
    visible: true
    color: "#0d0b09"
    title: "PiFire Dashboard — FPS Preview"

    // ---------------- Theme ----------------
    property var accents: ["Ember", "Ice", "Crimson"]
    property int accentIdx: 0
    property string accent: accents[accentIdx]
    function accentColor(a) { return a === "Ice" ? "#3cc7d0" : a === "Crimson" ? "#ff6a5a" : "#ff8a2b" }
    function glowColor(a) { return a === "Ice" ? "#2ec5d3" : a === "Crimson" ? "#ff5a4d" : "#ff7a1a" }
    property color accentCol: accentColor(accent)
    property color glowCol: glowColor(accent)

    readonly property color cardCol: "#1a1611"
    readonly property color insetCol: "#14100c"
    readonly property color borderCol: Qt.rgba(1, 1, 1, 0.05)
    readonly property color textCol: "#f4ede2"
    readonly property color dimCol: "#8a7f70"
    readonly property color labelCol: "#7d7264"
    readonly property color okCol: "#5ec96f"
    readonly property color warnCol: "#ffb020"
    readonly property color dangerCol: "#ff5a4d"
    readonly property color setpointCol: "#6cc8ff"

    // ---------------- Mode config ----------------
    property var modes: ["Startup", "Smoke", "Hold", "Monitor", "Shutdown", "Stop"]
    property int modeIdx: 1
    property string mode: modes[modeIdx]
    function cfg(m) {
        switch (m) {
        case "Startup":  return {label: "STARTUP",  sp: 0,   target: 165, fan: true,  auger: "run",   ign: true,  cooking: true}
        case "Smoke":    return {label: "SMOKE",    sp: 180, target: 183, fan: true,  auger: "cycle", ign: false, cooking: true}
        case "Hold":     return {label: "HOLD",     sp: 225, target: 225, fan: true,  auger: "cycle", ign: false, cooking: true}
        case "Monitor":  return {label: "MONITOR",  sp: 0,   target: 0,   fan: false, auger: "off",   ign: false, cooking: false}
        case "Shutdown": return {label: "SHUTDOWN", sp: 0,   target: 95,  fan: true,  auger: "off",   ign: false, cooking: false}
        default:         return {label: "STOP",     sp: 0,   target: 74,  fan: false, auger: "off",   ign: false, cooking: false}
        }
    }
    property var c: cfg(mode)
    onModeChanged: c = cfg(mode)

    property bool animate: true
    property int probeCount: 3
    property bool lidOpen: false

    // Mode-dependent control buttons (mirrors the design's controlButtons()).
    function controlButtons(m) {
        switch (m) {
        case "Monitor":  return [{t: "Startup", k: "accent"}, {t: "Stop", k: "danger"}]
        case "Shutdown": return [{t: "Stop", k: "danger"}]
        case "Startup":
        case "Smoke":
        case "Hold":     return [{t: "Set Temp", k: "accent"}, {t: "Smoke+", k: "accent"}, {t: "Shutdown", k: "danger"}, {t: "Stop", k: "danger"}]
        default:         return [{t: "Prime", k: "accent"}, {t: "Startup", k: "accent"}, {t: "Monitor", k: "accent"}, {t: "Stop", k: "danger"}]
        }
    }
    function kColor(k) { return k === "danger" ? dangerCol : k === "ok" ? okCol : accentCol }

    // ---------------- Simulated live state ----------------
    property real primaryTemp: 74
    property real hopper: 74
    property bool augerOn: false
    property bool fanOn: false
    property bool igniterOn: false
    property int elapsed: 0
    property int tick: 0
    property string clock: ""

    function fmtElapsed(s) {
        var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = Math.floor(s % 60)
        return (h > 0 ? h + ":" : "") + ("0" + m).slice(-2) + ":" + ("0" + ss).slice(-2)
    }

    ListModel {
        id: probeModel
        ListElement { pname: "Brisket";   temp: 74; target: 203 }
        ListElement { pname: "Pork Butt"; temp: 72; target: 200 }
        ListElement { pname: "Ribs";      temp: 70; target: 195 }
        ListElement { pname: "Chicken";   temp: 71; target: 165 }
        ListElement { pname: "Ambient";   temp: 76; target: 0 }
    }

    Timer {
        interval: 1000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: {
            win.tick++
            win.clock = Qt.formatTime(new Date(), "hh:mm")
            var cc = win.c
            if (cc.target > 0)
                win.primaryTemp += (cc.target - win.primaryTemp) * 0.07 + (Math.random() - 0.5) * 1.4
            win.fanOn = cc.fan
            win.igniterOn = cc.ign
            if (cc.auger === "run") win.augerOn = true
            else if (cc.auger === "cycle") win.augerOn = (win.tick % 11) < 3
            else win.augerOn = false
            if (win.augerOn) win.hopper = Math.max(0, win.hopper - 0.15)
            if (cc.cooking) win.elapsed++
            for (var i = 0; i < probeModel.count; i++) {
                var row = probeModel.get(i)
                var t = row.temp
                if (row.target > 0 && cc.cooking && t < row.target - 0.3)
                    t += Math.max(0.05, (row.target - t) * 0.04) + Math.random() * 0.4
                else
                    t += (Math.random() - 0.5) * 0.2
                probeModel.setProperty(i, "temp", t)
            }
        }
    }

    // ---------------- Fonts (optional; falls back to system) ----------------
    FontLoader { id: barlow; source: "../static/font/Barlow-SemiBold.ttf" }
    FontLoader { id: barlowCond; source: "../static/font/BarlowSemiCondensed-Bold.ttf" }
    property string sans: barlow.status === FontLoader.Ready ? barlow.name : "sans-serif"
    property string cond: barlowCond.status === FontLoader.Ready ? barlowCond.name : sans

    // ---------------- Scaled design canvas ----------------
    // Everything below is authored at exactly 1280x720 and scaled uniformly to the
    // window, so the SAME design reuses crisply on larger 16:9 screens (the planned
    // followup: the real host reads the screen size and drives this scale).
    Item {
        id: canvas
        width: 1280
        height: 720
        anchors.centerIn: parent
        scale: Math.min(win.width / 1280, win.height / 720)

    // ---------------- Background ----------------
    Rectangle {
        anchors.fill: parent
        gradient: Gradient {
            GradientStop { position: 0.0; color: "#0d0b09" }
            GradientStop { position: 1.0; color: "#1c140d" }
        }
    }
    // NOTE: the design has a soft radial glow along the bottom edge (CSS blur).
    // QML needs QtQuick.Effects/RadialGradient for that; a hard-edged shape reads
    // as a stray oval, so the preview omits it. The real build renders the glow
    // properly (Qt) / bakes it into the background PNG (pygame).

    // ---------------- Root layout ----------------
    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // ===== Header =====
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 58
            color: "transparent"
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Qt.rgba(1, 1, 1, 0.06) }
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 22
                anchors.rightMargin: 22
                Rectangle {
                    width: 12; height: 12; radius: 6
                    color: win.c.cooking ? win.okCol : win.labelCol
                    SequentialAnimation on opacity {
                        running: win.animate; loops: Animation.Infinite
                        NumberAnimation { to: 0.35; duration: 1200; easing.type: Easing.InOutQuad }
                        NumberAnimation { to: 1.0; duration: 1200; easing.type: Easing.InOutQuad }
                    }
                }
                Text {
                    text: "Pi<font color='" + win.accentCol + "'>Fire</font>"
                    textFormat: Text.RichText
                    font.family: win.sans; font.pixelSize: 20; font.bold: true; color: win.textCol
                    Layout.leftMargin: 12
                }
                Text {
                    text: "CONTROLLER"; font.family: win.sans; font.pixelSize: 12; font.letterSpacing: 2
                    color: win.labelCol; Layout.leftMargin: 10
                }
                Item { Layout.fillWidth: true }
                Text { text: "192.168.1.42"; font.family: win.sans; font.pixelSize: 13; color: win.dimCol }
                Text {
                    text: win.clock; font.family: win.cond; font.pixelSize: 22; color: "#cfc6b8"
                    Layout.leftMargin: 18
                }
                Rectangle {
                    Layout.leftMargin: 18
                    width: 44; height: 44; radius: 12; color: "#1d1813"; border.color: Qt.rgba(1, 1, 1, 0.08)
                    Column {
                        anchors.centerIn: parent; spacing: 4
                        Repeater { model: 3; Rectangle { width: 20; height: 2; radius: 2; color: "#cfc6b8" } }
                    }
                    TapHandler { onTapped: win.modeIdx = (win.modeIdx + 1) % win.modes.length }
                }
            }
        }

        // ===== Body =====
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.leftMargin: 18
            Layout.rightMargin: 18
            Layout.topMargin: 16
            Layout.bottomMargin: 18
            spacing: 16

            // ----- Left: food probes (collapses when none). Fixed-width panel:
            // pin the width so RowLayout can't derive it from child content. -----
            ColumnLayout {
                Layout.preferredWidth: 298
                Layout.minimumWidth: 298
                Layout.maximumWidth: 298
                Layout.fillHeight: true
                spacing: 12
                visible: win.probeCount > 0
                Text {
                    text: "FOOD PROBES"; font.family: win.sans; font.pixelSize: 13; font.letterSpacing: 2.5
                    color: win.labelCol; Layout.leftMargin: 4
                }
                Repeater {
                    model: Math.min(win.probeCount, probeModel.count)
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        color: win.cardCol; radius: 18; border.color: win.borderCol
                        property var row: probeModel.get(index)
                        property bool done: row.target > 0 && row.temp >= row.target - 1
                        Column {
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.leftMargin: 18
                            anchors.rightMargin: 18
                            spacing: 4
                            // header: name (left) + target (right) via anchors — no width feedback
                            Item {
                                width: parent.width
                                height: nameT.implicitHeight
                                Text {
                                    id: nameT
                                    anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
                                    text: row.pname.toUpperCase(); font.family: win.sans; font.pixelSize: 15
                                    font.letterSpacing: 1.5; color: "#b7ac9c"
                                }
                                Text {
                                    anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                    text: row.target > 0 ? "→ " + row.target + "°" : "AMBIENT"
                                    font.family: win.sans; font.pixelSize: 15
                                    color: row.target > 0 ? (done ? win.okCol : "#ffd23f") : win.labelCol
                                }
                            }
                            Row {
                                spacing: 2
                                Text { text: Math.round(row.temp); font.family: win.cond; font.pixelSize: 66; font.bold: true; color: win.textCol }
                                Text { text: "°F"; font.family: win.cond; font.pixelSize: 26; color: win.dimCol; anchors.bottom: parent.bottom; anchors.bottomMargin: 8 }
                            }
                            Rectangle {
                                width: parent.width; height: 6; radius: 3; color: Qt.rgba(1, 1, 1, 0.06)
                                Rectangle {
                                    height: parent.height; radius: 3
                                    width: parent.width * (row.target > 0 ? Math.max(0.02, Math.min(1, row.temp / row.target)) : 0)
                                    color: done ? win.okCol : win.accentCol
                                    Behavior on width { NumberAnimation { duration: 900; easing.type: Easing.OutCubic } }
                                }
                            }
                        }
                        TapHandler { onTapped: {} }
                    }
                }
                Item { Layout.fillHeight: true }
            }

            // ----- Center (absorbs horizontal slack via fillWidth) -----
            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 14

                Rectangle {  // gauge card: absorbs vertical slack in the center column
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    color: win.cardCol; radius: 22; border.color: win.borderCol
                    clip: true

                    Rectangle {  // glow disc
                        anchors.centerIn: parent
                        width: 340; height: 340; radius: 170; color: win.glowCol; opacity: 0.28
                        SequentialAnimation on scale {
                            running: win.animate && win.c.cooking; loops: Animation.Infinite
                            NumberAnimation { to: 1.06; duration: 1600; easing.type: Easing.InOutQuad }
                            NumberAnimation { to: 1.0; duration: 1600; easing.type: Easing.InOutQuad }
                        }
                    }

                    Shape {
                        id: gauge
                        anchors.centerIn: parent
                        width: 392; height: 392
                        readonly property real cx: width / 2
                        readonly property real cy: height / 2
                        readonly property real r: 160
                        readonly property real frac: Math.max(0, Math.min(1, win.primaryTemp / 600))
                        // NOTE: QML strokes can't take a gradient, so the value arc is a
                        // solid accent stroke + glow (the likely-shipping fallback). A true
                        // gradient arc would need a segmented/Canvas draw.
                        ShapePath {
                            strokeColor: "#2a241d"; strokeWidth: 28; fillColor: "transparent"; capStyle: ShapePath.RoundCap
                            PathAngleArc { centerX: gauge.cx; centerY: gauge.cy; radiusX: gauge.r; radiusY: gauge.r; startAngle: 135; sweepAngle: 270 }
                        }
                        ShapePath {
                            strokeColor: win.accentCol; strokeWidth: 28; fillColor: "transparent"; capStyle: ShapePath.RoundCap
                            PathAngleArc {
                                centerX: gauge.cx; centerY: gauge.cy; radiusX: gauge.r; radiusY: gauge.r
                                startAngle: 135
                                sweepAngle: 270 * gauge.frac
                                Behavior on sweepAngle { NumberAnimation { duration: 900; easing.type: Easing.OutCubic } }
                            }
                        }
                    }

                    // Setpoint marker — same angle convention as the arc (135° + 270°·frac,
                    // measured clockwise from 3 o'clock, screen y-down), drawn as a radial line.
                    Shape {
                        id: spMarker
                        anchors.centerIn: gauge
                        width: gauge.width; height: gauge.height
                        visible: win.c.sp > 0
                        antialiasing: true
                        property real a: (135 + 270 * Math.max(0, Math.min(1, win.c.sp / 600))) * Math.PI / 180
                        property real cx: width / 2
                        property real cy: height / 2
                        ShapePath {
                            strokeColor: win.setpointCol
                            strokeWidth: 4
                            capStyle: ShapePath.RoundCap
                            fillColor: "transparent"
                            startX: spMarker.cx + (gauge.r - 13) * Math.cos(spMarker.a)
                            startY: spMarker.cy + (gauge.r - 13) * Math.sin(spMarker.a)
                            PathLine {
                                x: spMarker.cx + (gauge.r + 9) * Math.cos(spMarker.a)
                                y: spMarker.cy + (gauge.r + 9) * Math.sin(spMarker.a)
                            }
                        }
                    }

                    Column {
                        anchors.centerIn: parent
                        spacing: 2
                        Text { anchors.horizontalCenter: parent.horizontalCenter; text: "GRILL"; font.family: win.sans; font.pixelSize: 14; font.letterSpacing: 4; color: win.labelCol }
                        Row {
                            anchors.horizontalCenter: parent.horizontalCenter; spacing: 4
                            Text { text: Math.round(win.primaryTemp); font.family: win.cond; font.pixelSize: 112; font.bold: true; color: "#f8f2e8" }
                            Text { text: "°F"; font.family: win.cond; font.pixelSize: 40; color: win.dimCol; anchors.bottom: parent.bottom; anchors.bottomMargin: 14 }
                        }
                        Text {
                            anchors.horizontalCenter: parent.horizontalCenter; visible: win.c.sp > 0
                            text: "SET " + win.c.sp + "°"; font.family: win.sans; font.pixelSize: 20; color: win.setpointCol
                        }
                        Rectangle {
                            anchors.horizontalCenter: parent.horizontalCenter
                            height: 34; width: pillText.width + 40; radius: 17
                            color: Qt.rgba(win.accentCol.r, win.accentCol.g, win.accentCol.b, 0.14)
                            border.color: Qt.rgba(win.accentCol.r, win.accentCol.g, win.accentCol.b, 0.55); border.width: 1.5
                            Text { id: pillText; anchors.centerIn: parent; text: win.c.label; font.family: win.sans; font.pixelSize: 17; font.bold: true; font.letterSpacing: 3; color: win.accentCol }
                        }
                    }
                }

                // cook time + lid (LID OPEN appears only when lidOpen; cook-time reflows to full width)
                RowLayout {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 52
                    Layout.maximumHeight: 52
                    spacing: 14
                    Rectangle {
                        Layout.fillWidth: true; Layout.fillHeight: true
                        color: win.cardCol; radius: 14; border.color: win.borderCol
                        RowLayout {
                            anchors.fill: parent; anchors.leftMargin: 20; anchors.rightMargin: 20
                            Text { text: "COOK TIME"; font.family: win.sans; font.pixelSize: 12; font.letterSpacing: 2; color: win.labelCol }
                            Item { Layout.fillWidth: true }
                            Text { text: win.fmtElapsed(win.elapsed); font.family: win.cond; font.pixelSize: 26; font.bold: true; color: "#cfc6b8" }
                        }
                    }
                    Rectangle {
                        Layout.preferredWidth: 210; Layout.fillHeight: true
                        visible: win.lidOpen
                        radius: 14; color: Qt.rgba(1, 0.35, 0.3, 0.14)
                        border.color: win.dangerCol; border.width: 1.5
                        Text { anchors.centerIn: parent; text: "LID OPEN"; font.family: win.sans; font.pixelSize: 20; font.bold: true; font.letterSpacing: 2; color: "#ff8b82" }
                        SequentialAnimation on opacity {
                            running: win.animate && win.lidOpen; loops: Animation.Infinite
                            NumberAnimation { to: 0.4; duration: 500 }
                            NumberAnimation { to: 1.0; duration: 500 }
                        }
                    }
                }

                // control buttons (mode-dependent count)
                RowLayout {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 82
                    Layout.maximumHeight: 82
                    spacing: 12
                    Repeater {
                        model: win.controlButtons(win.mode)
                        Rectangle {
                            Layout.fillWidth: true; Layout.fillHeight: true
                            radius: 16; color: "#1d1813"
                            border.width: 2
                            border.color: win.kColor(modelData.k)
                            Text { anchors.centerIn: parent; text: modelData.t; font.family: win.sans; font.pixelSize: 25; font.bold: true; color: "#e8dfd1" }
                        }
                    }
                }
            }

            // ----- Right (fixed-width panel; pinned) -----
            ColumnLayout {
                Layout.preferredWidth: 300
                Layout.minimumWidth: 300
                Layout.maximumWidth: 300
                Layout.fillHeight: true
                spacing: 14

                // System card
                Rectangle {
                    Layout.fillWidth: true
                    color: win.cardCol; radius: 18; border.color: win.borderCol
                    implicitHeight: sysCol.implicitHeight + 32
                    Column {
                        id: sysCol
                        anchors.fill: parent; anchors.margins: 16; spacing: 10
                        Text { text: "SYSTEM"; font.family: win.sans; font.pixelSize: 13; font.letterSpacing: 2.5; color: win.labelCol }
                        // Fan row
                        Rectangle {
                            width: parent.width; height: 66; radius: 13; color: win.insetCol
                            border.color: win.fanOn ? Qt.rgba(win.accentCol.r, win.accentCol.g, win.accentCol.b, 0.35) : win.borderCol
                            Row {
                                anchors.fill: parent; anchors.leftMargin: 14; anchors.rightMargin: 14; spacing: 14
                                Item {
                                    width: 46; height: 46; anchors.verticalCenter: parent.verticalCenter
                                    Shape {
                                        anchors.centerIn: parent; width: 46; height: 46
                                        transformOrigin: Item.Center
                                        RotationAnimation on rotation { running: win.animate && win.fanOn; from: 0; to: 360; duration: 850; loops: Animation.Infinite }
                                        ShapePath {
                                            fillColor: win.fanOn ? win.accentCol : "#57514a"; strokeColor: "transparent"
                                            PathSvg { path: "M23 23 Q 13 14 16 5 Q 23 2 23 23 Z" }
                                            PathSvg { path: "M23 23 Q 36 19 42 27 Q 40 35 23 23 Z" }
                                            PathSvg { path: "M23 23 Q 20 36 11 39 Q 5 33 23 23 Z" }
                                        }
                                    }
                                }
                                Column {
                                    anchors.verticalCenter: parent.verticalCenter
                                    Text { text: "FAN"; font.family: win.sans; font.pixelSize: 17; color: "#cfc6b8" }
                                    Text { text: win.fanOn ? "RUNNING" : "IDLE"; font.family: win.sans; font.pixelSize: 13; font.letterSpacing: 2; color: win.fanOn ? win.accentCol : win.labelCol }
                                }
                            }
                            Rectangle { anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter; width: 9; height: 9; radius: 5; color: win.fanOn ? win.okCol : "#4a443c" }
                        }
                        // Auger row
                        Rectangle {
                            width: parent.width; height: 66; radius: 13; color: win.insetCol
                            border.color: win.augerOn ? Qt.rgba(win.accentCol.r, win.accentCol.g, win.accentCol.b, 0.35) : win.borderCol
                            Row {
                                anchors.fill: parent; anchors.leftMargin: 14; spacing: 14
                                Item {
                                    width: 60; height: 40; anchors.verticalCenter: parent.verticalCenter
                                    Row {
                                        spacing: 6
                                        Repeater {
                                            model: 5
                                            Rectangle {
                                                width: 5; height: 26; radius: 2; rotation: 24
                                                color: win.augerOn ? win.accentCol : "#57514a"
                                                anchors.verticalCenter: parent.verticalCenter
                                                SequentialAnimation on opacity {
                                                    running: win.animate && win.augerOn; loops: Animation.Infinite
                                                    PauseAnimation { duration: index * 90 }
                                                    NumberAnimation { to: 0.3; duration: 250 }
                                                    NumberAnimation { to: 1.0; duration: 250 }
                                                    PauseAnimation { duration: (5 - index) * 90 }
                                                }
                                            }
                                        }
                                    }
                                }
                                Column {
                                    anchors.verticalCenter: parent.verticalCenter
                                    Text { text: "AUGER"; font.family: win.sans; font.pixelSize: 17; color: "#cfc6b8" }
                                    Text { text: win.augerOn ? "FEEDING" : "IDLE"; font.family: win.sans; font.pixelSize: 13; font.letterSpacing: 2; color: win.augerOn ? win.accentCol : win.labelCol }
                                }
                            }
                            Rectangle { anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter; width: 9; height: 9; radius: 5; color: win.augerOn ? win.okCol : "#4a443c" }
                        }
                        // Igniter row
                        Rectangle {
                            width: parent.width; height: 66; radius: 13; color: win.insetCol
                            border.color: win.igniterOn ? Qt.rgba(1, 0.48, 0.1, 0.4) : win.borderCol
                            Row {
                                anchors.fill: parent; anchors.leftMargin: 14; spacing: 14
                                Item {
                                    width: 46; height: 40; anchors.verticalCenter: parent.verticalCenter
                                    Text {
                                        anchors.centerIn: parent; text: "♨"; font.pixelSize: 34
                                        color: win.igniterOn ? "#ff7a1a" : "#57514a"
                                        SequentialAnimation on opacity {
                                            running: win.animate && win.igniterOn; loops: Animation.Infinite
                                            NumberAnimation { to: 0.55; duration: 120 }
                                            NumberAnimation { to: 0.92; duration: 140 }
                                            NumberAnimation { to: 0.6; duration: 110 }
                                            NumberAnimation { to: 1.0; duration: 160 }
                                        }
                                    }
                                }
                                Column {
                                    anchors.verticalCenter: parent.verticalCenter
                                    Text { text: "IGNITER"; font.family: win.sans; font.pixelSize: 17; color: "#cfc6b8" }
                                    Text { text: win.igniterOn ? "HOT" : "OFF"; font.family: win.sans; font.pixelSize: 13; font.letterSpacing: 2; color: win.igniterOn ? "#ff7a1a" : win.labelCol }
                                }
                            }
                            Rectangle { anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter; width: 9; height: 9; radius: 5; color: win.igniterOn ? "#ff7a1a" : "#4a443c" }
                        }
                    }
                }

                // Duty / status pills (mode-aware): Hold -> AUGER/FAN DUTY, else P-MODE/SMOKE+
                RowLayout {
                    id: dutyRow
                    Layout.fillWidth: true
                    Layout.preferredHeight: 64
                    Layout.maximumHeight: 64
                    spacing: 14
                    readonly property bool hold: win.mode === "Hold"
                    readonly property bool rightOn: hold ? win.fanOn : win.c.cooking
                    Rectangle {  // left pill: P-MODE / AUGER DUTY (neutral)
                        Layout.fillWidth: true; Layout.fillHeight: true; radius: 14
                        color: win.cardCol; border.color: win.borderCol
                        Column {
                            anchors.centerIn: parent
                            Text { anchors.horizontalCenter: parent.horizontalCenter; text: dutyRow.hold ? "AUGER DUTY" : "P-MODE"; font.family: win.sans; font.pixelSize: 10; font.letterSpacing: 1.5; color: win.labelCol }
                            Text { anchors.horizontalCenter: parent.horizontalCenter; text: dutyRow.hold ? Math.round(17 + 4 * Math.sin(win.tick / 4)) + "%" : "P-2"; font.family: win.cond; font.pixelSize: 24; font.bold: true; color: win.accentCol }
                        }
                    }
                    Rectangle {  // right pill: SMOKE+ / FAN DUTY (highlights when on)
                        Layout.fillWidth: true; Layout.fillHeight: true; radius: 14
                        color: dutyRow.rightOn ? Qt.rgba(win.okCol.r, win.okCol.g, win.okCol.b, 0.14) : win.cardCol
                        border.color: dutyRow.rightOn ? win.okCol : win.borderCol
                        Column {
                            anchors.centerIn: parent
                            Text { anchors.horizontalCenter: parent.horizontalCenter; text: dutyRow.hold ? "FAN DUTY" : "SMOKE+"; font.family: win.sans; font.pixelSize: 10; font.letterSpacing: 1.5; color: dutyRow.rightOn ? "#8fe09a" : win.labelCol }
                            Text { anchors.horizontalCenter: parent.horizontalCenter; text: dutyRow.hold ? (win.fanOn ? "100%" : "0%") : (win.c.cooking ? "ON" : "OFF"); font.family: win.cond; font.pixelSize: 24; font.bold: true; color: dutyRow.rightOn ? "#8fe09a" : win.labelCol }
                        }
                    }
                }

                // Hopper (vertical fill; threshold colors)
                Rectangle {
                    id: hopperCard
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    color: win.cardCol; radius: 18; border.color: win.borderCol
                    readonly property color hopCol: win.hopper < 15 ? win.dangerCol : win.hopper < 35 ? win.warnCol : win.okCol
                    Column {
                        anchors.fill: parent; anchors.margins: 16; spacing: 12
                        Item {
                            width: parent.width
                            height: hp.implicitHeight
                            Text {
                                anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
                                text: "HOPPER"; font.family: win.sans; font.pixelSize: 13; font.letterSpacing: 2.5; color: win.labelCol
                            }
                            Text {
                                id: hp
                                anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                text: Math.round(win.hopper) + "%"; font.family: win.cond; font.pixelSize: 34; font.bold: true; color: hopperCard.hopCol
                            }
                        }
                        Rectangle {
                            width: parent.width; height: parent.height - 78; radius: 14
                            color: Qt.rgba(1, 1, 1, 0.045); border.color: Qt.rgba(1, 1, 1, 0.04); clip: true
                            Rectangle {
                                anchors.bottom: parent.bottom; width: parent.width
                                height: parent.height * win.hopper / 100
                                color: hopperCard.hopCol
                                Behavior on height { NumberAnimation { duration: 900; easing.type: Easing.OutCubic } }
                            }
                        }
                        Text {
                            text: win.hopper < 15 ? "REFILL PELLETS" : win.hopper < 35 ? "RUNNING LOW" : "LEVEL OK"
                            font.family: win.sans; font.pixelSize: 12; font.letterSpacing: 2; color: hopperCard.hopCol
                        }
                    }
                    TapHandler { onTapped: {} }
                }
            }
        }
    }
    }
    // ---------------- end scaled design canvas ----------------

    // ---------------- Click-through mode cycle ----------------
    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton
        onClicked: win.modeIdx = (win.modeIdx + 1) % win.modes.length
        propagateComposedEvents: true
    }

    // ---------------- Keyboard ----------------
    Item {
        anchors.fill: parent
        focus: true
        Keys.onPressed: (e) => {
            if (e.key === Qt.Key_A) win.accentIdx = (win.accentIdx + 1) % win.accents.length
            else if (e.key === Qt.Key_M) win.modeIdx = (win.modeIdx + 1) % win.modes.length
            else if (e.key === Qt.Key_P) win.probeCount = win.probeCount > 0 ? 0 : 3
            else if (e.key === Qt.Key_L) win.lidOpen = !win.lidOpen
            else if (e.key === Qt.Key_F) win.animate = !win.animate
        }
    }

    // ---------------- FPS counter ----------------
    FrameAnimation {
        id: frameAnim
        running: true
        property int frames: 0
        onTriggered: frames++
    }
    Timer {
        interval: 1000; running: true; repeat: true
        onTriggered: { fpsLabel.text = frameAnim.frames + " FPS"; frameAnim.frames = 0 }
    }
    Rectangle {
        anchors.top: parent.top; anchors.right: parent.right
        anchors.topMargin: 66; anchors.rightMargin: 12
        width: fpsLabel.width + 20; height: 30; radius: 8; color: "#000000cc"
        Text { id: fpsLabel; objectName: "fpsLabel"; anchors.centerIn: parent; text: "-- FPS"; color: "#7ef0d2"; font.family: win.cond; font.pixelSize: 18; font.bold: true }
    }
    Text {
        anchors.bottom: parent.bottom; anchors.left: parent.left; anchors.margins: 6
        text: "click/M: mode(" + win.mode + ") · A: accent(" + win.accent + ") · P: probes(" + win.probeCount + ") · L: lid(" + (win.lidOpen ? "open" : "closed") + ") · F: anim(" + (win.animate ? "on" : "off") + ")"
        color: Qt.rgba(1, 1, 1, 0.35); font.family: win.sans; font.pixelSize: 12
    }
}
