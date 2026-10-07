import QtQuick
import qs.Commons

// One measure against one denominator: a slim track with a fill. With no
// reading (fraction null) the track is outlined and empty, never a zero-length
// fill. `endTicks` marks the track's two ends, so the denominator's extent is
// visible even when the fill is tiny. A positive reading always shows at least
// a dot. Flat colours only: no glow or gradient changes a measured length.
Item {
  id: meter
  property var fraction: null
  property color fill: foreground
  property color foreground: Color.foreground
  property bool endTicks: false
  property int animationDuration: 0
  readonly property bool hasReading: fraction !== null && fraction !== undefined && !isNaN(fraction)
  property real shown: hasReading ? Math.max(0, Math.min(1, Number(fraction))) : 0
  Behavior on shown { NumberAnimation { duration: meter.animationDuration; easing.type: Easing.OutCubic } }

  implicitHeight: Math.max(2, Style.space(4))
  implicitWidth: Style.space(40)

  Rectangle {
    id: track
    anchors.fill: parent
    radius: height / 2
    color: meter.hasReading ? Util.alpha(meter.foreground, 0.12) : "transparent"
    border.width: meter.hasReading ? 0 : Math.max(1, Style.space(1))
    border.color: Util.alpha(meter.foreground, 0.32)
  }
  Rectangle {
    anchors.left: parent.left
    anchors.top: parent.top
    anchors.bottom: parent.bottom
    visible: meter.hasReading && meter.shown > 0
    width: Math.max(height, track.width * meter.shown)
    radius: height / 2
    color: meter.fill
  }
  Repeater {
    model: meter.endTicks ? 2 : 0
    Rectangle {
      required property int index
      x: index === 0 ? -width - Style.space(1) : track.width + Style.space(1)
      anchors.verticalCenter: parent.verticalCenter
      width: Math.max(1, Style.space(1))
      height: track.height + Style.space(4)
      color: Util.alpha(meter.foreground, 0.35)
    }
  }
}
