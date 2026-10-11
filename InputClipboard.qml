import QtQuick

// One explicit read per shared runtime. Never subscribes to the native clipboard.
QtObject {
  id: root
  property var runtime: null
  property string clientId: ""
  property int generation: 0
  property string requestId: ""
  property var actor: null
  signal result(var recipient, string text, string error)

  function read(recipient, limit, primary) {
    if (!runtime || !runtime.storageHelperAvailable || actor) return false
    if (!clientId) clientId = runtime.newClientId("paste")
    actor = recipient
    requestId = "p:" + clientId + ":" + (++generation)
    if (!runtime.sendCommand({ command: "input.paste", requestId: requestId,
        clientId: clientId, generation: generation, limit: limit, primary: !!primary })) {
      actor = null; requestId = ""
      return false
    }
    timeout.start()
    return true
  }
  function cancel(recipient) {
    if (!actor || actor !== recipient) return
    timeout.stop()
    actor = null; requestId = ""
    if (runtime) runtime.sendCommand({ command: "input.cancel", requestId: "p:" + clientId + ":" + (++generation),
      clientId: clientId, generation: generation })
  }
  function accept(message) {
    if (!actor || message.requestId !== requestId || message.clientId !== clientId || message.generation !== generation) return
    var recipient = actor
    timeout.stop()
    actor = null; requestId = ""
    if (message.type === "input-result" && typeof message.text === "string") result(recipient, message.text, "")
    else result(recipient, "", message.message || "Paste unavailable; type your text instead.")
  }
  property Timer timeout: Timer {
    interval: 2500
    onTriggered: {
      var recipient = root.actor
      root.cancel(recipient)
      if (recipient) root.result(recipient, "", "Paste took too long; type your text instead.")
    }
  }
  property Connections replies: Connections {
    target: root.runtime
    function onInputMessage(message) { root.accept(message) }
    function onStorageRestarted() {
      var recipient = root.actor
      root.cancel(recipient)
      if (recipient) root.result(recipient, "", "Helper restarted; try Paste again.")
    }
  }
}
