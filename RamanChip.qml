import QtQuick
import qs.Commons
import qs.Ui
import "Design.js" as Design

// One choice in a row of choices: page buttons, History windows, Apps lenses.
// Selected: filled, bold and lit by a short accent line along its base (the
// one neon accent in the panel chrome). Keyboard focus: a full 2 px outline.
// Unavailable choices stay readable, dimmed, with the reason in their text or
// tooltip rather than struck through. Callers set the Accessible role.
BorderSurface {
  id: chip
  property string text: ""
  property bool selected: false
  property bool focused: false
  property bool available: true
  property color foreground: Color.foreground
  property color accent: Design.ensureContrast(Color.accent, Color.popups.background, Design.MARK_CONTRAST)
  property string fontFamily: Style.font.family
  property real fontSize: Style.font.bodySmall
  property real padX: Style.space(9)
  readonly property bool hovered: chipMouse.containsMouse
  readonly property color dim: Qt.darker(foreground, 1.45)

  signal clicked()

  implicitWidth: chipLabel.implicitWidth + padX * 2
  implicitHeight: Math.max(Style.space(24), chipLabel.implicitHeight + Style.space(8))
  radius: Style.cornerRadius
  opacity: available ? 1 : 0.6
  color: selected ? Util.alpha(foreground, hovered ? 0.18 : 0.12)
    : Util.alpha(foreground, hovered && available ? 0.08 : 0)
  borderSpec: Border.flat(focused ? foreground : Util.alpha(foreground, selected ? 0.32 : 0.14),
    focused ? Math.max(2, Style.normalBorderWidth * 2) : Style.normalBorderWidth)

  Text {
    id: chipLabel
    anchors.centerIn: parent
    textFormat: Text.PlainText
    text: chip.text
    color: chip.selected ? chip.foreground : chip.dim
    font.family: chip.fontFamily
    font.pixelSize: chip.fontSize
    font.bold: chip.selected
  }

  // The lit line: inset from the rounded corners, sitting on the border.
  Rectangle {
    visible: chip.selected
    anchors.horizontalCenter: parent.horizontalCenter
    anchors.bottom: parent.bottom
    anchors.bottomMargin: chip.focused ? Math.max(2, Style.normalBorderWidth * 2) : Style.normalBorderWidth
    width: Math.max(Style.space(8), chipLabel.implicitWidth - Style.space(4))
    height: Math.max(2, Style.space(2))
    radius: height / 2
    color: chip.accent
  }

  MouseArea {
    id: chipMouse
    anchors.fill: parent
    hoverEnabled: true
    cursorShape: chip.available ? Qt.PointingHandCursor : Qt.ArrowCursor
    onClicked: chip.clicked()
  }
}
