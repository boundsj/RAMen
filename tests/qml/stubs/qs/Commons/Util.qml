pragma Singleton
import QtQuick
QtObject {
  function alpha(c, opacity) { var q = Qt.color(c); return Qt.rgba(q.r, q.g, q.b, q.a * opacity) }
}
