import QtQuick
import QtTest
import "../.."

TestCase {
  id: tc
  name: "BoundedInput"
  when: windowShown
  width: 500; height: 250; visible: true
  QtObject {
    id: runtime
    property bool storageHelperAvailable: true
    property var commands: []
    signal inputMessage(var message)
    signal storageRestarted()
    function newClientId(kind) { return kind + "-one" }
    function sendCommand(command) { commands = commands.concat([command]); return true }
  }
  InputClipboard { id: driver; runtime: runtime }
  RamanTextField { id: field; x: 20; y: 20; width: 200; maximumLength: 8; clipboard: driver }
  RamanTextField { id: other; x: 20; y: 90; width: 200; clipboard: driver }
  function init() {
    driver.cancel(driver.actor)
    runtime.commands = []
    field.visible = true; field.text = ""; field.inputError = ""
    field.forceActiveFocus()
  }
  function reply(text) {
    var c = runtime.commands[runtime.commands.length - 1]
    runtime.inputMessage({ type: "input-result", requestId: c.requestId,
      clientId: c.clientId, generation: c.generation, text: text })
  }
  function test_native_editing_and_undo() {
    keyClick(Qt.Key_A); keyClick(Qt.Key_B); keyClick(Qt.Key_C)
    compare(field.text, "abc")
    keyClick(Qt.Key_Left); keyClick(Qt.Key_Backspace)
    compare(field.text, "ac")
    keyClick(Qt.Key_Z, Qt.ControlModifier)
    compare(field.text, "abc")
    keyClick(Qt.Key_A, Qt.ControlModifier); keyClick(Qt.Key_X, Qt.ControlModifier)
    compare(field.text, "")
  }
  function test_paste_is_explicit_and_bounded() {
    compare(runtime.commands.length, 0)
    field.text = "abc"; field.select(1,2)
    keyClick(Qt.Key_V, Qt.ControlModifier)
    compare(runtime.commands.length, 1)
    compare(runtime.commands[0].command, "input.paste")
    compare(runtime.commands[0].limit, 8)
    reply("雪🍜")
    compare(field.text, "a雪🍜c")
    compare(field.cursorPosition, 4)
    field.selectAll(); keyClick(Qt.Key_Insert, Qt.ShiftModifier); reply("ninechars")
    compare(field.text, "a雪🍜c")
    verify(field.inputError.length > 0)
  }
  function test_late_reply_cannot_edit_changed_or_hidden_field() {
    field.text = "a"; field.cursorPosition = 1
    field.paste(); var old = runtime.commands[0]
    keyClick(Qt.Key_B)
    compare(runtime.commands[1].command, "input.cancel")
    runtime.inputMessage({type: "input-result", requestId: old.requestId, clientId: old.clientId,
      generation: old.generation, text: "late"})
    compare(field.text, "ab")
    field.paste(); field.visible = false
    compare(driver.actor, null)
    compare(field.pendingPaste, null)
  }
  function test_focus_and_restart_cancel_and_primary_uses_worker() {
    field.paste(); other.forceActiveFocus()
    compare(driver.actor, null)
    field.forceActiveFocus(); field.requestPaste(true)
    compare(runtime.commands[runtime.commands.length-1].primary, true)
    runtime.storageRestarted()
    compare(driver.actor, null)
    verify(field.inputError.indexOf("restarted") >= 0)
  }
  function test_navigation_during_read_drops_reply() {
    field.text = "abc"; field.cursorPosition = 3
    field.paste(); keyClick(Qt.Key_Left)
    compare(driver.actor, null)
    compare(field.text, "abc")
  }
  function test_long_path_scrolls_and_unicode_is_plain_text() {
    field.maximumLength = 2048
    field.text = "x".repeat(300) + "雪🍜"; field.cursorPosition = field.text.length
    var editor = findChild(field, "boundedEditor")
    var viewport = editor.parent.parent
    tryVerify(function() { return viewport.contentX > 0 })
    verify(editor.cursorRectangle.x - viewport.contentX <= viewport.width)
    field.maximumLength = 8
    field.text = "雪🍜"; field.cursorPosition = field.text.length
    keyClick(Qt.Key_Backspace)
    compare(field.text, "雪", "native editing deletes a complete surrogate pair")
  }
  function test_enter_never_inserts_line_break_and_limits_hold() {
    field.text = "12345678"; field.cursorPosition = 8
    keyClick(Qt.Key_A)
    compare(field.text, "12345678")
    keyClick(Qt.Key_Return)
    compare(field.text, "12345678")
    field.text = "bad\nline"
    compare(field.text, "12345678")
    field.text = "\u2028"
    compare(field.text, "12345678")
  }
  function test_error_and_identity_do_not_leak_to_other_field() {
    field.paste(); var c = runtime.commands[0]
    runtime.inputMessage({type: "input-result", requestId: "wrong", clientId: c.clientId,
      generation: c.generation, text: "wrong"})
    compare(field.text, "")
    verify(driver.actor !== null)
    runtime.inputMessage({type: "error", requestId: c.requestId, clientId: c.clientId,
      generation: c.generation, message: "Clipboard took too long"})
    compare(field.inputError, "Clipboard took too long")
    compare(other.inputError, "")
  }
}
