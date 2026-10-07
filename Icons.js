.pragma library

// RAMen's vector icons and the bowl mark, as SVG path data. RamanIcon.qml and
// RamanBowl.qml draw them with Qt Quick Shapes, so no meaning depends on a
// Nerd Font glyph being installed. Icons use a 16-unit box and a 1.5-unit
// round stroke; tests/test_design.js checks every entry, and
// scripts/render-icons.js writes the same data to docs/art/icons.svg.

var BOX = 16
var STROKE = 1.5

var ICONS = {
  // Kill (SIGTERM): a plain close cross.
  close: { stroke: "M4.5 4.5 L11.5 11.5 M11.5 4.5 L4.5 11.5" },
  // Force kill (SIGKILL): the cross inside a stop-sign octagon.
  force: { stroke: "M5.4 1.75 H10.6 L14.25 5.4 V10.6 L10.6 14.25 H5.4 L1.75 10.6 V5.4 Z M6 6 L10 10 M10 6 L6 10" },
  // Protected: a padlock.
  lock: { stroke: "M5.25 7.25 V5.25 C5.25 3.6 6.5 2.25 8 2.25 C9.5 2.25 10.75 3.6 10.75 5.25 V7.25 "
            + "M4.25 7.25 H11.75 Q12.75 7.25 12.75 8.25 V12.75 Q12.75 13.75 11.75 13.75 H4.25 "
            + "Q3.25 13.75 3.25 12.75 V8.25 Q3.25 7.25 4.25 7.25 Z M8 9.75 V11.25" },
  // Details: "i" in a circle.
  info: { stroke: "M1.75 8 A6.25 6.25 0 1 0 14.25 8 A6.25 6.25 0 1 0 1.75 8 Z M8 7.25 V11.5 M8 4.75 V4.8" },
  back: { stroke: "M10 3.5 L5.5 8 L10 12.5" },
  next: { stroke: "M6 3.5 L10.5 8 L6 12.5" },
  search: { stroke: "M2.5 7 A4.5 4.5 0 1 0 11.5 7 A4.5 4.5 0 1 0 2.5 7 Z M10.25 10.25 L13.75 13.75" },
  // Incident marker: three rising wisps of steam. It marks that something was
  // recorded; it encodes no value and never animates.
  steam: { stroke: "M3.5 14.5 C1.5 11.5 5.5 9.5 3.5 6 M8 14.5 C6 11 10 9 8 4.5 M12.5 14.5 C10.5 11.5 14.5 9.5 12.5 6" }
}

function icon(name) {
  return ICONS.hasOwnProperty(name) ? ICONS[name] : null
}

// The bowl mark in an 18 x 16 box: rim, body, foot and two chopsticks resting
// on the rim; steam rises at the left. The body is two cubic curves meeting at
// the base, kept steep so the broth level reads like a gauge.
var BOWL = {
  width: 18,
  height: 16,
  rimY: 4.5,
  baseY: 13.5,
  // Left half of the body: rim corner -> base centre (the right half mirrors it).
  left: [[1.5, 4.5], [1.5, 11.6], [3.2, 13.5], [9, 13.5]],
  rim: "M0.5 4.5 H17.5",
  foot: "M6 15.5 H12",
  chopsticks: "M10 3.7 L14.4 0.4 M12.4 4 L16.8 0.7",
  steam: ["M3.5 3.4 C2.8 2.5 4.2 1.6 3.5 0.4", "M6 3.4 C5.3 2.5 6.7 1.6 6 0.4"]
}

function bowlBody() {
  var l = BOWL.left, w = BOWL.width
  function mirror(p) { return (w - p[0]) + " " + p[1] }
  return "M" + l[0].join(" ") + " C" + l[1].join(" ") + " " + l[2].join(" ") + " " + l[3].join(" ")
    + " C" + mirror(l[2]) + " " + mirror(l[1]) + " " + mirror(l[0])
}
