import QtQuick

// RAMen's shared service: Omarchy mounts one instance per shell for a plugin
// whose manifest lists the `service` kind (shell.qml ensureService), however
// many monitors show the bar widget, and destroys it on plugin hot-reload.
// Bar widgets reach it with bar.shell.serviceFor("boundsj.raman").
//
// The one probe, history writer and IPC target therefore live here. Panel
// hotkeys go through the host's summon/hide/toggle, which pick the widget on
// the focused monitor.
Item {
  id: root
  visible: false

  // Injected by the host: a facade scoped to this plugin's own id.
  property var shell: null
  property var manifest: null

  readonly property alias runtime: runtime

  function hostCall(method) {
    if (root.shell && typeof root.shell[method] === "function") return root.shell[method]("boundsj.raman")
    return false
  }

  RamanRuntime {
    id: runtime
    mode: "service"
    panelRouter: ({
      open: function() { root.hostCall("summon") },
      close: function() { root.hostCall("hide") },
      toggle: function() { root.hostCall("toggle") }
    })
  }
}
