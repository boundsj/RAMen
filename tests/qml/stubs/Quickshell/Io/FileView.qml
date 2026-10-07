import QtQuick
QtObject {
  property string path: ""
  property bool watchChanges: false
  property bool printErrors: true
  signal loaded()
  signal fileChanged()
  function reload() {}
  function text() { return "" }
}
