import QtQuick
import QtTest
import qs.Commons
import "../.."

TestCase {
  name: "BarWidgetOwnership"
  when: windowShown
  width: 800; height: 600

  Component { id: serviceComponent; Service {} }
  Component { id: widgetComponent; BarWidget {} }

  Component {
    id: barComponent
    QtObject {
      property var shell: null
      property color barForeground: Color.foreground
      property color foreground: Color.foreground
      property color urgent: Color.urgent
      property string fontFamily: "DejaVu Sans Mono"
      property bool vertical: false
      property int barSize: 26
      property string position: "top"
      property var activePopout: null
      property bool foregroundAnimationEnabled: true
      property bool centerSectionRevealHeld: false
      property bool centerHoverRevealSuppressed: false
      function registerClickTarget(t) {}
      function unregisterClickTarget(t) {}
      function showTooltip(t, x) {}
      function hideTooltip(t) {}
      function requestPopout(o) {}
      function releasePopout(o) {}
    }
  }
  function fakeBar(service) {
    return createTemporaryObject(barComponent, null, {
      shell: service === undefined ? null : { serviceFor: function(id) { return id === "boundsj.raman" ? service : null } }
    })
  }
  function probeOf(rt) {
    for (var i = 0; i < rt.data.length; i++) if (rt.data[i].stdinEnabled === true) return rt.data[i]
    return null
  }

  function test_reduced_motion_and_local_off() {
    var svc = createTemporaryObject(serviceComponent, null)
    var w = createTemporaryObject(widgetComponent, this, { bar: fakeBar(svc), settings: { animations: "off" } })
    compare(w.animationDuration, 0)
    w.settings = { animations: "on" }
    compare(w.animationDuration, 300)
  }

  function test_two_monitors_share_the_service_runtime() {
    var svc = createTemporaryObject(serviceComponent, null)
    var left = createTemporaryObject(widgetComponent, this, { bar: fakeBar(svc), settings: { warnPercent: 40 } })
    var right = createTemporaryObject(widgetComponent, this, { bar: fakeBar(svc), settings: { warnPercent: 40 } })
    tryVerify(function() { return left.runtime === svc.runtime && right.runtime === svc.runtime })
    compare(left.useLocalRuntime, false)
    compare(svc.runtime.widgets.length, 2)
    probeOf(svc.runtime).feed(JSON.stringify({ type: "summary", total: 100, used: 50, available: 50, psiSome10: 0 }))
    compare(left.summary.used, 50)
    compare(right.level, "warn", "settings reach the shared runtime")
    // Legacy setDetail from each monitor uses its own key.
    left.setDetail(true); right.setDetail(true)
    left.setDetail(false)
    compare(svc.runtime.detail, true, "closing the left panel keeps the right panel's scan")
    right.setDetail(false)
    compare(svc.runtime.detail, false)
    right.destroy()
    tryVerify(function() { return svc.runtime.widgets.length === 1 })
  }

  function test_destroyed_widget_releases_its_subscription() {
    var svc = createTemporaryObject(serviceComponent, null)
    var w = createTemporaryObject(widgetComponent, this, { bar: fakeBar(svc) })
    tryVerify(function() { return w.runtime === svc.runtime })
    w.setDetail(true)
    compare(svc.runtime.detail, true)
    w.destroy()
    tryCompare(svc.runtime, "detail", false)
  }

  function test_late_service_uses_fresh_subscription_keys() {
    var svc = createTemporaryObject(serviceComponent, null)
    var shared = createTemporaryObject(widgetComponent, this, { bar: fakeBar(svc) })
    shared.setDetail(true)
    var late = createTemporaryObject(widgetComponent, this, { bar: fakeBar(undefined) })
    tryVerify(function() { return late.useLocalRuntime && late.runtime !== null }, 4000)
    late.setDetail(true)
    var original = late.runtime
    verify(original.detail)
    late.bar = fakeBar(svc)
    tryVerify(function() { return late.runtime === svc.runtime })
    verify(shared.detailKey !== late.detailKey)
    late.setDetail(false)
    compare(svc.runtime.detail, true, "moving/closing the late subscriber leaves the first one active")
    shared.setDetail(false)
    compare(svc.runtime.detail, false)
  }

  function test_falls_back_to_a_private_runtime_without_a_service() {
    var w = createTemporaryObject(widgetComponent, this, { bar: fakeBar(undefined) })
    tryVerify(function() { return w.runtime !== null }, 4000)
    compare(w.useLocalRuntime, true)
    compare(w.runtime.mode, "widget")
  }
}
