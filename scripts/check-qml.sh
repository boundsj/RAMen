#!/bin/sh
# Offscreen behavior/render checks. Requires Qt 6.8+ and Omarchy's shell sources;
# replaces process/IPC/layer-shell effects with test doubles. No live changes.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
shell_root=${RAMAN_QML_SHELL:-$HOME/.local/share/omarchy/shell}
runner=${RAMAN_QML_RUNNER:-qmltestrunner}
if [ ! -f "$shell_root/Ui/PanelKeyCatcher.qml" ]; then
  echo "Set RAMAN_QML_SHELL to Omarchy v4.0.4's shell directory." >&2
  exit 1
fi
artifact_root=${RAMAN_QML_ARTIFACTS:-$root/.agent-artifacts}
mkdir -p "$artifact_root"
stage=$(mktemp -d "$artifact_root/qml-check.XXXXXX")
mkdir -p "$stage/tests/qml" "$stage/stubs" "$stage/shots/gallery"
cp "$root"/*.qml "$root"/*.js "$root"/*.py "$stage/"
cp -R "$root/tests/qml/." "$stage/tests/qml/"
cp -R "$root/tests/qml/stubs/." "$stage/stubs/"
# Use the host UI as a dependency, fetched/installed separately. These files
# are never copied into tracked source. Keep its upstream license in the stage.
cp "$shell_root"/../LICENSE "$stage/OMARCHY-LICENSE"
for name in BarWidget Panel PanelController PanelKeyCatcher PanelHero PanelSeparator PanelSectionHeader PanelActionButton PanelToolTip BorderSurface CursorSurface WidgetButton BorderOverlay; do
  cp "$shell_root/Ui/$name.qml" "$stage/stubs/qs/Ui/"
done
cp "$shell_root/Commons/Border.qml" "$shell_root/Commons/BorderGeometry.js" "$stage/stubs/qs/Commons/"
cp "$shell_root/plugins/notifications/NotificationLogic.js" "$stage/tests/HostNotificationLogic.js"
mkdir -p "$stage/docs"
cp "$root/docs/demo-scenes.json" "$stage/docs/"
PYTHONDONTWRITEBYTECODE=1 python3 "$root/tests/make_qml_fixtures.py" "$stage/tests/fixtures"
export QT_QPA_PLATFORM=${QT_QPA_PLATFORM:-offscreen}
export QML_XHR_ALLOW_FILE_READ=1
export PYTHONDONTWRITEBYTECODE=1
"$runner" -import "$stage/stubs" -input "$stage/tests/qml" "$@"
echo "QML artifacts: $stage"
