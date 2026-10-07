import QtQuick
// Test stub of Quickshell.Io.Process: records writes, never runs anything.
QtObject {
  property var command: []
  property bool running: false
  property bool stdinEnabled: false
  property var stdout: null
  property var processId: running ? 4242 : null
  property var written: []
  property var startedCommands: []
  signal started()
  signal exited(int code, int status)
  onRunningChanged: if (running) { startedCommands = startedCommands.concat([command]); started() }
  function write(text) { written = written.concat([text]) }
  // Test hook: deliver one stdout line.
  function feed(line) { if (stdout) stdout.read(line) }
}
