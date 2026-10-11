import QtQuick
import QtQuick.Controls
import "InputModel.js" as Input

// TextEdit's clipboard-change callback checks formats only (unlike TextInput,
// which reads text). Keep native Unicode/IME editing; own every paste gesture.
FocusScope {
  id: field
  property alias text: editor.text
  property alias font: editor.font
  property alias color: editor.color
  property alias placeholderText: editor.placeholderText
  property alias placeholderTextColor: editor.placeholderTextColor
  property alias selectionColor: editor.selectionColor
  property alias selectedTextColor: editor.selectedTextColor
  property alias selectByMouse: editor.selectByMouse
  property alias cursorPosition: editor.cursorPosition
  readonly property alias selectionStart: editor.selectionStart
  readonly property alias selectionEnd: editor.selectionEnd
  property real leftPadding: 6
  property real rightPadding: 6
  property real topPadding: 6
  property real bottomPadding: 6
  property Item background: null
  onBackgroundChanged: if (background) { background.parent = field; background.anchors.fill = field; background.z = -1 }
  function select(start, end) { editor.select(start, end) }
  function selectAll() { editor.selectAll() }
  function copy() { editor.copy() }
  function undo() { editor.undo() }
  function redo() { editor.redo() }
  property var clipboard: null
  property int maximumLength: 2048
  property string inputError: ""
  property var pendingPaste: null
  property string validText: ""
  property int validCursor: 0
  property bool restoring: false
  signal accepted()
  signal boundedEdited()
  clip: true
  implicitHeight: fontMetrics.height + topPadding + bottomPadding
  Flickable {
    id: viewport
    x: field.leftPadding; y: field.topPadding
    width: Math.max(0, field.width - field.leftPadding - field.rightPadding)
    height: Math.max(0, field.height - field.topPadding - field.bottomPadding)
    clip: true
    interactive: false
    boundsBehavior: Flickable.StopAtBounds
    TextArea.flickable: TextArea {
      id: editor
      objectName: "boundedEditor"
      focus: true
      padding: 0
      background: null
      textFormat: TextEdit.PlainText
      wrapMode: TextEdit.NoWrap
      selectByMouse: true
      Keys.priority: Keys.BeforeItem
      Keys.onPressed: function(event) { field.handleKey(event) }
    }
  }
  FontMetrics { id: fontMetrics; font: field.font }
  ToolTip.visible: activeFocus && inputError !== ""
  ToolTip.text: inputError
  ToolTip.timeout: 4000

  function invalidatePaste() {
    if (clipboard) clipboard.cancel(field)
    pendingPaste = null
  }
  function requestPaste(primary) {
    invalidatePaste()
    pendingPaste = { text: text, start: selectionStart, end: selectionEnd, cursor: cursorPosition }
    inputError = ""
    if (!clipboard || !clipboard.read(field, maximumLength, primary)) {
      pendingPaste = null
      inputError = "Paste unavailable; type your text instead."
    }
  }
  // QML callers also use the bounded path; never invoke native paste().
  function paste() { requestPaste(false) }
  function receivePaste(recipient, value, error) {
    if (recipient !== field || !pendingPaste) return
    var before = pendingPaste
    pendingPaste = null
    if (!activeFocus || !visible || text !== before.text || cursorPosition !== before.cursor
        || selectionStart !== before.start || selectionEnd !== before.end) return
    if (error) { inputError = error; return }
    var changed = Input.replace(text, before.start, before.end, value, maximumLength)
    if (!changed) { inputError = "Paste is too long or contains a line break."; return }
    // Native document operations retain undo; assigning text would clear it.
    editor.remove(before.start, before.end)
    editor.insert(before.start, value)
    cursorPosition = changed.cursor
  }
  onTextChanged: {
    if (restoring) return
    invalidatePaste()
    if (!Input.valid(text, maximumLength)) {
      restoring = true
      text = validText
      cursorPosition = validCursor
      restoring = false
      return
    }
    validText = text
    validCursor = cursorPosition
    inputError = ""
    boundedEdited()
  }
  Connections {
    target: field
    function onActiveFocusChanged() { if (!field.activeFocus) field.invalidatePaste() }
    function onVisibleChanged() { if (!field.visible) field.invalidatePaste() }
  }
  Component.onDestruction: invalidatePaste()
  Connections {
    target: field.clipboard
    function onResult(recipient, text, error) { field.receivePaste(recipient, text, error) }
  }
  MouseArea {
    id: middleArea
    anchors.fill: parent
    acceptedButtons: Qt.MiddleButton
    onClicked: function(mouse) {
      field.forceActiveFocus()
      var point = editor.mapFromItem(middleArea, mouse.x, mouse.y)
      field.cursorPosition = editor.positionAt(point.x, point.y)
      field.requestPaste(true)
    }
  }
  function handleKey(event) {
    if (event.matches(StandardKey.Paste)) {
      requestPaste(false); event.accepted = true; return
    }
    if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
      accepted(); event.accepted = true; return
    }
    invalidatePaste()
    event.accepted = false
  }
}
