import QtQuick
import QtQuick.Shapes
import "Icons.js" as Icons
import "Design.js" as Design

// RAMen's bowl mark, which is also its memory gauge. Broth fills the bowl to
// `fraction` of used memory: a linear level from the inner base to the rim
// (Design.brothY), in the level colour. `steam` wisps (0-2) repeat tight and
// critical as a shape, so the state never depends on colour alone. With no
// reading (fraction null) only the outline shows. Only the caller's
// level/colour transitions move; the mark itself never animates.
Item {
  id: root
  property var fraction: null
  property color fill: "white"
  property color line: "white"
  property int steam: 0
  property int animationDuration: 0
  readonly property bool hasReading: fraction !== null && fraction !== undefined && !isNaN(fraction)
  readonly property real unit: height / Icons.BOWL.height
  // At least one device-independent pixel, however small the mark.
  readonly property real strokeUnits: unit > 0 ? Math.max(1, 1 / unit) : 1
  property real level: hasReading ? Math.max(0, Math.min(1, Number(fraction))) : 0
  Behavior on level { NumberAnimation { duration: root.animationDuration; easing.type: Easing.OutCubic } }

  implicitHeight: 16
  implicitWidth: Math.round(height * Icons.BOWL.width / Icons.BOWL.height)
  Accessible.ignored: true

  Item {
    width: Icons.BOWL.width
    height: Icons.BOWL.height
    scale: root.unit
    transformOrigin: Item.TopLeft

    Item {
      id: broth
      visible: root.hasReading && root.level > 0
      y: Design.brothY(root.level)
      width: parent.width
      height: Math.max(0, Icons.BOWL.height - y)
      clip: true
      Shape {
        y: -broth.y
        width: Icons.BOWL.width
        height: Icons.BOWL.height
        preferredRendererType: Shape.CurveRenderer
        antialiasing: true
        ShapePath {
          strokeColor: "transparent"
          fillColor: root.fill
          PathSvg { path: Icons.bowlBody() + " Z" }
        }
      }
    }

    Shape {
      anchors.fill: parent
      preferredRendererType: Shape.CurveRenderer
      antialiasing: true
      ShapePath {
        strokeColor: root.line
        strokeWidth: root.strokeUnits
        fillColor: "transparent"
        capStyle: ShapePath.RoundCap
        joinStyle: ShapePath.RoundJoin
        PathSvg { path: Icons.BOWL.rim + " " + Icons.bowlBody() + " " + Icons.BOWL.foot + " " + Icons.BOWL.chopsticks }
      }
      ShapePath {
        strokeColor: root.steam > 0 && root.hasReading ? root.fill : "transparent"
        strokeWidth: root.strokeUnits
        fillColor: "transparent"
        capStyle: ShapePath.RoundCap
        PathSvg { path: root.steam > 0 ? Icons.BOWL.steam.slice(0, Math.min(2, root.steam)).join(" ") : "M0 0" }
      }
    }
  }
}
