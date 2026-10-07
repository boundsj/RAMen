import QtQuick
import QtTest
import "../../HistoryModel.js" as HM
import "../HostNotificationLogic.js" as HostLogic

TestCase {
  name: "NotificationText"
  Component { id: textComponent; Text { font.family: "DejaVu Sans Mono"; font.pixelSize: 14 } }

  function test_app_names_remain_literal_in_host_styled_text() {
    var name = '<b>Fake system warning</b> &amp; spoof "quoted" \'single\' <img src="file:///private">'
    var ack = { persisted: true, incident: { severity: "warn", reason: "used" },
      measurement: { total: 100, used: 80, available: 20 },
      notificationContext: { status: "observed", at: 1000, apps: [{ name: name, kb: 1024 }] } }
    var toast = HM.incidentToast(ack, 10, { detail: true, now: 1001000 })
    var styled = HostLogic.styledBody(toast.body, "RAMen", "")
    verify(styled.indexOf("<b>") < 0)
    verify(styled.indexOf("<img") < 0)
    verify(styled.indexOf("&amp;amp;") >= 0)
    var rendered = createTemporaryObject(textComponent, this, { text: styled, textFormat: Text.StyledText })
    var literal = createTemporaryObject(textComponent, this, {
      text: "RAM used 80% for 10 s · 20K available · Observed: " + name + " 1M", textFormat: Text.PlainText })
    compare(rendered.contentWidth, literal.contentWidth, "entities must decode once to the literal app name")
    compare(rendered.contentHeight, literal.contentHeight)
  }
}
