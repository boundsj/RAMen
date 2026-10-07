pragma Singleton
import QtQuick
// Test stub of Omarchy's Color singleton.
QtObject {
  property var shellValues: ({})
  property color foreground: "#cacccc"
  property color background: "#101315"
  property color accent: "#7aa2f7"
  property color urgent: "#a55555"
  property color muted: "#707880"
  property QtObject popups: QtObject { property color background: "#101315"; property color text: "#cacccc"; property color border: "#7aa2f7" }
  property QtObject tooltip: QtObject { property color background: "#101315"; property color text: "#cacccc"; property color border: "#cacccc" }
  function setTheme(light) {
    foreground = light ? "#343b58" : "#cacccc"
    background = light ? "#e6e7ed" : "#101315"
    popups.background = background; popups.text = foreground
    tooltip.background = background; tooltip.text = foreground
  }
}
