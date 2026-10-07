import QtQuick
import QtQuick.Shapes
import "Icons.js" as Icons
import "Design.js" as Design

// One of Icons.js's vector icons, stroked in `color` at `size` logical pixels.
// Drawn with Shapes, so it renders the same with or without a Nerd Font. In a
// list or other scrolling view it hides while scrolled wholly out of sight
// (see Design.inViewport: the software renderer would paint it unclipped).
Item {
  id: root
  property string name: ""
  property color color: "white"
  property real size: 16
  // Small icons keep at least this stroke in logical pixels.
  property real minStroke: 1.1
  readonly property var spec: Icons.icon(name)
  readonly property bool inView: Design.inViewport(root, Design.scroller(root))
  implicitWidth: size
  implicitHeight: size

  Shape {
    width: Icons.BOX
    height: Icons.BOX
    scale: root.size / Icons.BOX
    transformOrigin: Item.TopLeft
    visible: !!root.spec && root.inView
    preferredRendererType: Shape.CurveRenderer
    antialiasing: true
    ShapePath {
      strokeColor: root.color
      strokeWidth: Math.max(Icons.STROKE, root.minStroke * Icons.BOX / Math.max(1, root.size))
      fillColor: "transparent"
      capStyle: ShapePath.RoundCap
      joinStyle: ShapePath.RoundJoin
      PathSvg { path: root.spec ? root.spec.stroke : "M0 0" }
    }
  }
}
