import QtQuick
import qs.Commons
// Test stub of Omarchy's KeyboardPanel: an Item instead of a layer-shell
// window, so the card can be rendered offscreen and grabbed.
Item {
  id: root
  property var anchorItem: null
  property var bar: null
  property var owner: null
  property bool open: false
  property int padding: Style.spacing.popupPadding
  property int contentWidth: 280
  property int contentHeight: 200
  property Item focusTarget: null
  property real availableCardWidth: 2000
  property real availableCardHeight: 1400
  default property alias contentItem: holder.children
  width: contentWidth
  height: contentHeight
  visible: open
  function fittedContentWidth(w, cap) { var m = availableCardWidth; if (cap) m = Math.min(m, cap); return Math.round(Math.min(w, m)) }
  function fittedContentHeight(h, cap) { var d = h + padding * 2 + 2; var m = availableCardHeight; if (cap) m = Math.min(m, cap); return Math.round(Math.min(d, m)) }
  onOpenChanged: if (open && focusTarget) Qt.callLater(function() { root.focusTarget.forceActiveFocus() })
  Rectangle { anchors.fill: parent; color: Color.popups.background; border.color: Color.popups.border; border.width: 1; radius: Style.cornerRadius }
  Item { id: holder; anchors.fill: parent; anchors.margins: root.padding + 1 }
}
