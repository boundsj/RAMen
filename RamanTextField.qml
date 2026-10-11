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
  property string placeholderText: ""
  property color placeholderTextColor: "#808080"
  property color menuSurface: "white"
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
  property int validStart: 0
  property int validEnd: 0
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
    contentWidth: Math.max(width, editor.contentWidth)
    contentHeight: Math.max(height, editor.contentHeight)
    TextEdit {
      id: editor
      objectName: "boundedEditor"
      focus: true
      width: Math.max(viewport.width, contentWidth)
      height: Math.max(viewport.height, contentHeight)
      textFormat: TextEdit.PlainText
      wrapMode: TextEdit.NoWrap
      selectByMouse: true
      activeFocusOnTab: true
      // Plain TextEdit has no Controls TextArea default clipboard menu.
      onCursorRectangleChanged: field.ensureCaret()
      Keys.priority: Keys.BeforeItem
      Keys.onPressed: function(event) { field.handleKey(event) }
    }
  }
  Text {
    x: field.leftPadding; y: field.topPadding
    width: Math.max(0, field.width - field.leftPadding - field.rightPadding)
    height: Math.max(0, field.height - field.topPadding - field.bottomPadding)
    visible: editor.length === 0 && editor.preeditText.length === 0
    text: field.placeholderText; textFormat: Text.PlainText; elide: Text.ElideRight
    font: field.font; color: field.placeholderTextColor
    verticalAlignment: Text.AlignVCenter
  }
  FontMetrics { id: fontMetrics; font: field.font }
  function ensureCaret() {
    var caret = editor.cursorRectangle
    var x = viewport.contentX
    if (caret.x < x) x = caret.x
    else if (caret.x + caret.width > x + viewport.width) x = caret.x + caret.width - viewport.width
    viewport.contentX = Math.max(0, Math.min(viewport.contentWidth - viewport.width, x))
  }
  function rememberCursor() {
    // Qt can move selection before invalidating its cached text property.
    // Read the document directly so a rejected edit cannot replace this receipt.
    if (!restoring && editor.length === validText.length && editor.getText(0, editor.length) === validText) {
      validCursor = cursorPosition; validStart = selectionStart; validEnd = selectionEnd
    }
  }
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
      var cursor = validCursor, start = validStart, end = validEnd
      // Revert the rejected native edit without replacing the document or
      // clearing earlier undo. Programmatic assignment already resets Qt undo.
      if (editor.canUndo) editor.undo()
      if (text !== validText) text = validText
      editor.select(cursor === start ? end : start, cursor)
      restoring = false
      return
    }
    validText = text
    rememberCursor()
    inputError = ""
    boundedEdited()
  }
  Connections {
    target: field
    function onActiveFocusChanged() { if (!field.activeFocus) field.invalidatePaste() }
    function onVisibleChanged() { if (!field.visible) field.invalidatePaste() }
  }
  Connections {
    target: editor
    function onCursorPositionChanged() { field.rememberCursor() }
    function onSelectionStartChanged() { field.rememberCursor() }
    function onSelectionEndChanged() { field.rememberCursor() }
  }
  Component.onDestruction: invalidatePaste()
  Connections {
    target: field.clipboard
    function onResult(recipient, text, error) { field.receivePaste(recipient, text, error) }
  }
  MouseArea {
    id: middleArea
    anchors.fill: parent
    acceptedButtons: Qt.MiddleButton | Qt.RightButton
    onClicked: function(mouse) {
      field.forceActiveFocus()
      if (mouse.button === Qt.RightButton) contextMenu.popup(mouse.x, mouse.y)
      else {
        var point = editor.mapFromItem(middleArea, mouse.x, mouse.y)
        field.cursorPosition = editor.positionAt(point.x, point.y)
        field.requestPaste(true)
      }
    }
  }
  Menu {
    id: contextMenu
    objectName: "boundedInputMenu"
    font: field.font
    palette.window: field.menuSurface
    palette.text: field.color
    palette.windowText: field.color
    palette.buttonText: field.color
    palette.highlightedText: field.color
    palette.highlight: Qt.rgba(field.color.r, field.color.g, field.color.b, 0.12)
    // Close synchronously before restoring editor focus for an async paste.
    enter: Transition {}
    exit: Transition {}
    MenuItem { text: "Undo"; enabled: editor.canUndo; onTriggered: editor.undo() }
    MenuItem { text: "Redo"; enabled: editor.canRedo; onTriggered: editor.redo() }
    MenuSeparator {}
    MenuItem { text: "Cut"; enabled: editor.selectedText.length > 0; onTriggered: editor.cut() }
    MenuItem { text: "Copy"; enabled: editor.selectedText.length > 0; onTriggered: editor.copy() }
    MenuItem {
      objectName: "boundedPasteAction"
      text: "Paste"; enabled: !!field.clipboard
      onTriggered: {
        contextMenu.close()
        Qt.callLater(function() { field.forceActiveFocus(); field.requestPaste(false) })
      }
    }
    MenuItem { text: "Select all"; onTriggered: editor.selectAll() }
  }
  function handleKey(event) {
    if (event.key === Qt.Key_Tab || event.key === Qt.Key_Backtab) {
      invalidatePaste()
      var next = editor.nextItemInFocusChain(!(event.modifiers & Qt.ShiftModifier) && event.key !== Qt.Key_Backtab)
      if (next) next.forceActiveFocus(Qt.TabFocusReason)
      event.accepted = true; return
    }
    if (event.key === Qt.Key_Menu || event.key === Qt.Key_F10 && (event.modifiers & Qt.ShiftModifier)) {
      contextMenu.popup(); event.accepted = true; return
    }
    if (event.matches(StandardKey.Paste)) {
      requestPaste(false); event.accepted = true; return
    }
    if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
      accepted(); event.accepted = true; return
    }
    invalidatePaste()
    // Refusing a key before insertion preserves native undo at the limit.
    // IME commits are checked by onTextChanged; AltGr remains native.
    var ctrl = !!(event.modifiers & Qt.ControlModifier)
    var alt = !!(event.modifiers & Qt.AltModifier)
    if ((!ctrl || alt) && !(event.modifiers & Qt.MetaModifier) && event.text
        && Input.valid(event.text, maximumLength)
        && !Input.replace(text, selectionStart, selectionEnd, event.text, maximumLength)) {
      event.accepted = true; return
    }
    event.accepted = false
  }
}
