import QtQuick
import QtQuick.Controls
import qs.Commons

// A command button (Scan, Back, Next, Copy path...), with an optional leading
// vector icon. Disabled buttons stay legible at reduced opacity. `text` is the
// command's name and its accessible name.
Button {
  id: control
  property color foreground: Color.foreground
  property string fontFamily: Style.font.family
  property real fontSize: Style.font.bodySmall
  property string iconName: ""
  property bool primary: false

  implicitHeight: Math.max(Style.space(26), label.implicitHeight + Style.space(10))
  implicitWidth: row.implicitWidth + Style.space(18)
  padding: Style.space(4)
  opacity: enabled ? 1 : 0.45
  Accessible.name: text

  background: Rectangle {
    radius: Style.cornerRadius
    color: Util.alpha(control.foreground, control.down ? 0.24 : control.hovered ? 0.15 : control.primary ? 0.1 : 0.05)
    border.width: control.visualFocus ? Math.max(2, Style.normalBorderWidth * 2) : Style.normalBorderWidth
    border.color: control.visualFocus ? control.foreground : Util.alpha(control.foreground, control.primary ? 0.45 : 0.25)
  }
  contentItem: Item {
    implicitWidth: row.implicitWidth
    implicitHeight: label.implicitHeight
    Row {
      id: row
      anchors.centerIn: parent
      spacing: Style.space(5)
      RamanIcon {
        visible: control.iconName !== ""
        anchors.verticalCenter: parent.verticalCenter
        name: control.iconName
        size: Math.round(control.fontSize * 1.1)
        color: control.foreground
      }
      Text {
        id: label
        anchors.verticalCenter: parent.verticalCenter
        width: Math.min(implicitWidth, Math.max(0, control.availableWidth - (control.iconName !== "" ? control.fontSize * 1.1 + Style.space(5) : 0)))
        textFormat: Text.PlainText
        text: control.text
        color: control.foreground
        font.family: control.fontFamily
        font.pixelSize: control.fontSize
        font.bold: control.primary
        elide: Text.ElideRight
      }
    }
  }
}
