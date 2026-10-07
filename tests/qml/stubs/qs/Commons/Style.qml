pragma Singleton
import QtQuick
// Test stub of Omarchy's Style singleton (v4.0.4 has no reduceMotion/duration()).
QtObject {
  property real scale: 1
  property int cornerRadius: 6
  property int gapsOut: 5
  property int normalBorderWidth: 1
  property int focusBorderWidth: 2
  property int hoverBorderWidth: 1
  property int selectedBorderWidth: 1
  property real normalBorderAlpha: 0.3
  property real focusBorderAlpha: 1
  property real hoverBorderAlpha: 0.7
  property real selectedBorderAlpha: 0.7
  property var styleOverrides: ({})
  function normalStateColor(fg, accent, urgent) { return fg }
  function focusStateColor(fg, accent, urgent) { return fg }
  function hoverStateColor(fg, accent, urgent) { return fg }
  function selectedStateColor(fg, accent, urgent) { return fg }
  function spaceReal(px) { return px * scale }
  function space(px) { return Math.round(px * scale) }
  function hoverFillFor(fg, accent) { return Qt.rgba(fg.r, fg.g, fg.b, 0.08) }
  function focusFillFor(fg, accent) { return Qt.rgba(fg.r, fg.g, fg.b, 0.10) }
  function selectedFillFor(fg, accent) { return Qt.rgba(fg.r, fg.g, fg.b, 0.14) }
  property QtObject font: QtObject {
    property string family: "DejaVu Sans Mono"
    property int caption: 10; property int bodySmall: 11; property int body: 12
    property int subtitle: 13; property int title: 14; property int heading: 16
    property int display: 24; property int icon: 14
  }
  property QtObject spacing: QtObject { property int rowPaddingX: 12; property int popupPadding: 14; property int sm: 4 }
  property QtObject bar: QtObject { property int iconCanvas: 16; property int sizeHorizontal: 26 }
}
