import QtQuick
import qs.Commons
import qs.Ui

// A row action with a vector icon (RamanIcon), using the host's hover-cursor
// tokens like Omarchy's PanelActionButton. Row actions are not keyboard
// targets themselves: the row's cursor owns the keys. `hasCursor` paints the
// keyboard cursor the same as pointer hover; `hovered` reports pointer moves.
BorderSurface {
  id: root
  property string iconName: ""
  property string tooltipText: ""
  property color foreground: Color.foreground
  property color hoverColor: foreground
  property string fontFamily: Style.font.family
  property real iconSize: Style.font.icon
  property real size: Math.max(Style.space(24), iconSize + Style.space(8))
  property bool hasCursor: false

  signal clicked()
  signal hovered(bool isHovered)

  readonly property bool hot: (mouse.containsMouse || hasCursor) && enabled
  implicitWidth: size
  implicitHeight: size
  radius: Style.cornerRadius
  color: hot ? Style.hoverFillFor(hoverColor, hoverColor) : "transparent"
  borderSpec: hot ? Border.controlSpec("hover-cursor", hoverColor, hoverColor) : Border.none()
  Accessible.role: Accessible.Button
  Accessible.name: tooltipText
  Accessible.onPressAction: if (root.enabled) root.clicked()

  RamanIcon {
    anchors.centerIn: parent
    name: root.iconName
    size: root.iconSize
    color: !root.enabled ? Qt.darker(root.foreground, 2.0) : root.hot ? root.hoverColor : root.foreground
  }

  MouseArea {
    id: mouse
    anchors.fill: parent
    hoverEnabled: true
    enabled: root.enabled
    cursorShape: root.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
    onContainsMouseChanged: root.hovered(containsMouse)
    onClicked: root.clicked()
  }

  PanelToolTip {
    visible: root.tooltipText !== "" && mouse.containsMouse
    text: root.tooltipText
    fontFamily: root.fontFamily
  }
}
