.pragma library

// Panel pages, focus regions and key routing, kept free of QML types so
// tests/test_panel_nav.js can check every page/region/key combination.
//
// One page is selected and one region has focus. Keys only act on the focused
// region: page buttons take h/l only while focused, the History chart takes
// h/l to scrub, incident receipts take j/k. Only the Memory and Apps app lists
// route Enter/x/f to the two-step kill (Apps only while its details view is
// closed); every other region ignores x and f. Tab/Shift+Tab never reach this
// router: they switch Omarchy bar panels.

// Apps comes last so h/l from Memory still reaches History first.
var PAGES = ["memory", "history", "storage", "apps"]
var PAGE_LABELS = { memory: "Memory", history: "History", storage: "Storage", apps: "Apps" }

// Regions top to bottom for each page. The Apps search field is a text
// editor: while it has focus no key reaches this router.
var REGIONS = {
  memory: ["nav", "list"],
  history: ["nav", "window", "chart", "incidents"],
  storage: ["nav", "ledger"],
  apps: ["nav", "sort", "list"]
}

// Points moved per H/L press on the History chart.
var JUMP = 12

// Actions that can reach the probe's kill commands. Only memory/list and
// apps/list (details closed) may emit them.
var SIGNAL_ACTIONS = ["activate", "arm", "armForce", "appsActivate", "appsArm", "appsArmForce"]

function initialState() {
  return { page: "memory", region: "list", inspecting: false }
}

function pageIndex(page) {
  var i = PAGES.indexOf(page)
  return i < 0 ? 0 : i
}

// The page `step` away from `page`, clamped (no wraparound, so l on the last
// page does nothing rather than jumping back to Memory).
function stepPage(page, step) {
  var i = Math.max(0, Math.min(PAGES.length - 1, pageIndex(page) + step))
  return PAGES[i]
}

function withState(state, changes) {
  var next = { page: state.page, region: state.region, inspecting: !!state.inspecting }
  for (var k in changes) next[k] = changes[k]
  return next
}

function result(state, action, arg) {
  return { state: state, action: action || null, arg: arg === undefined ? null : arg }
}

// Select a page from a mouse click or IPC; focus moves to its page button.
function selectPage(state, page) {
  if (PAGES.indexOf(page) < 0) return withState(state, {})
  return withState(state, { page: page, region: "nav", inspecting: false })
}

// key: "up" | "down" | "left" | "right" | "enter" | "delete" | "force" |
//      "refresh" | "escape" | "jumpLeft" | "jumpRight" (H/L: scrub faster) |
//      Apps only: "search" (/), "details" (d), "nextPage" (]), "prevPage" ([).
// ctx: { cursorActive, selectedIndex, incidentIndex, incidentCount,
//        inputOwned, appsIndex, appsDetails, appsArmed }.
// Returns { state, action, arg }; the panel performs the action.
function route(state, key, ctx) {
  var c = ctx || {}
  var s = withState(state || initialState(), {})
  if (s.page === "apps") return routeApps(s, key, c)
  if (s.page === "storage") {
    if (c.inputOwned) return result(s, null)
    if (s.region === "nav" && (key === "left" || key === "right")) {
      var target = stepPage(s.page, key === "left" ? -1 : 1)
      return result(withState(s, { page: target }), target !== s.page ? "page" : null, target)
    }
    if (s.region === "nav") {
      if (key === "down") return result(withState(s, { region: "ledger" }), "focus", "ledger")
      if (key === "escape") return result(s, "close")
      return result(s, null)
    }
    if (key === "up" && (c.storageIndex || 0) === 0)
      return result(withState(s, { region: "nav" }), "focus", "nav")
    var actions = { up: "storageMove", down: "storageMove", enter: "storageDescend", right: "storageDescend",
      left: "storageUp", backspace: "storageUp", refresh: "storageScan", cancel: "storageCancel",
      open: "storageOpen", copy: "storageCopy", escape: "storageEscape" }
    return result(withState(s, { region: "ledger" }), actions[key], key === "up" ? -1 : key === "down" ? 1 : null)
  }
  if (key === "refresh") return result(s, "refresh")

  if (s.page === "memory") {
    if (s.region === "nav") {
      if (key === "left" || key === "right") {
        var page = stepPage(s.page, key === "left" ? -1 : 1)
        return result(withState(s, { page: page }), page !== s.page ? "page" : null, page)
      }
      if (key === "down") return result(withState(s, { region: "list" }), "enterList")
      if (key === "escape") return result(s, "close")
      return result(s, null) // Enter/x/f on a page button never reach the kill path
    }
    // memory/list keeps the original panel behavior.
    if (key === "up") {
      if (!c.cursorActive || (c.selectedIndex || 0) === 0)
        return result(withState(s, { region: "nav" }), "leaveList")
      return result(s, "moveCursor", -1)
    }
    if (key === "down") return result(s, "moveCursor", 1)
    if (key === "enter") return result(s, "activate")
    if (key === "delete") return result(s, "arm")
    if (key === "force") return result(s, "armForce")
    if (key === "escape") return result(s, "escape")
    return result(s, null)
  }

  // History: read-only. No key here can produce a signal action.
  if (s.inspecting) {
    if (key === "escape") return result(withState(s, { inspecting: false, region: "incidents" }), "back")
    if (key === "up" || key === "down") return result(s, "moveIncident", key === "up" ? -1 : 1)
    return result(s, null)
  }
  if (key === "escape") return result(s, "close")
  var regions = REGIONS.history
  var at = regions.indexOf(s.region)
  if (at < 0) at = 0
  if (s.region === "nav" && (key === "left" || key === "right")) {
    var next = stepPage(s.page, key === "left" ? -1 : 1)
    return result(withState(s, { page: next, region: "nav" }), next !== s.page ? "page" : null, next)
  }
  if (s.region === "window" && (key === "left" || key === "right")) return result(s, "window", key === "left" ? -1 : 1)
  if (s.region === "chart" && (key === "left" || key === "right")) return result(s, "scrub", key === "left" ? -1 : 1)
  if (s.region === "chart" && (key === "jumpLeft" || key === "jumpRight")) return result(s, "scrub", key === "jumpLeft" ? -JUMP : JUMP)
  if (s.region === "chart" && key === "enter") return result(s, "inspectAtCursor")
  if (s.region === "incidents") {
    if (key === "enter") return (c.incidentCount || 0) > 0 ? result(withState(s, { inspecting: true }), "inspect") : result(s, null)
    if (key === "down") return result(s, "moveIncident", 1)
    if (key === "up" && (c.incidentIndex || 0) > 0) return result(s, "moveIncident", -1)
  }
  if (key === "up" && at > 0) return result(withState(s, { region: regions[at - 1] }), "focus", regions[at - 1])
  if (key === "down" && at < regions.length - 1) {
    var below = regions[at + 1]
    if (below === "incidents" && (c.incidentCount || 0) === 0) return result(s, null)
    return result(withState(s, { region: below }), "focus", below)
  }
  return result(s, null)
}

// Apps: page buttons -> sort lens -> list. Escape disarms first, then closes
// the details view, then the panel (the search field handles its own Escape).
function routeApps(s, key, c) {
  if (c.inputOwned) return result(s, null)
  if (key === "refresh") return result(s, "appsRefresh")
  if (key === "search") return result(s, "appsSearch")
  if (s.region === "nav") {
    if (key === "left" || key === "right") {
      var page = stepPage(s.page, key === "left" ? -1 : 1)
      return result(withState(s, { page: page }), page !== s.page ? "page" : null, page)
    }
    if (key === "down") return result(withState(s, { region: "sort" }), "focus", "sort")
    if (key === "escape") return result(s, "close")
    return result(s, null)
  }
  if (s.region === "sort") {
    if (key === "left" || key === "right") return result(s, "appsSort", key === "left" ? -1 : 1)
    if (key === "up") return result(withState(s, { region: "nav" }), "focus", "nav")
    if (key === "down") return result(withState(s, { region: "list" }), "appsEnterList")
    if (key === "nextPage" || key === "prevPage") return result(s, "appsPage", key === "nextPage" ? 1 : -1)
    if (key === "escape") return result(s, "close")
    return result(s, null)
  }
  if (c.appsDetails) {
    // Read-only subview: Enter/x/f never act here.
    if (key === "escape") return result(s, c.appsArmed ? "appsDisarm" : "appsCloseDetails")
    if (key === "details") return result(s, "appsCloseDetails")
    if (key === "up" || key === "down") return result(s, "appsScrollDetails", key === "up" ? -1 : 1)
    return result(s, null)
  }
  if (key === "up") {
    if ((c.appsIndex === undefined ? -1 : c.appsIndex) <= 0)
      return result(withState(s, { region: "sort" }), "appsLeaveList")
    return result(s, "appsMove", -1)
  }
  if (key === "down") return result(s, "appsMove", 1)
  if (key === "enter") return result(s, "appsActivate")
  if (key === "delete") return result(s, "appsArm")
  if (key === "force") return result(s, "appsArmForce")
  if (key === "details") return result(s, "appsDetails")
  if (key === "nextPage" || key === "prevPage") return result(s, "appsPage", key === "nextPage" ? 1 : -1)
  if (key === "escape") return result(s, c.appsArmed ? "appsDisarm" : "close")
  return result(s, null)
}

// The data a visible panel needs from the runtime for its page. Memory and
// Apps share the one detail scan; Apps also asks for GPU client sampling
// unless gpuMetrics is off. Memory never does, so a Memory-only panel causes
// no GPU work.
function subscriptionFor(opened, page, gpuMetrics) {
  if (!opened) return null
  if (page === "apps") return { detail: true, history: false, gpu: gpuMetrics !== "off" }
  if (page === "memory") return { detail: true, history: false }
  return page === "storage" ? { storage: true } : { detail: false, history: true }
}
