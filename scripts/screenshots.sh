#!/usr/bin/env bash
# Regenerate docs/screenshots/ from the canned scenes in docs/demo-scenes.json.
#
# Switches to an empty workspace so only the wallpaper sits behind the panel,
# replays each scene through the live widget (`omarchy-shell boundsj.raman
# demo <scene>`), and crops the bar item and the panel. The panel is located by
# diffing captures taken with it closed and open. Your previous workspace and
# live data are restored on exit. Needs grim, magick, jq and wtype.

set -euo pipefail

cd "$(dirname "$0")/.."
out=docs/screenshots
workspace=${RAMAN_SHOT_WORKSPACE:-9}
tmp=$(mktemp -d)
mkdir -p "$out"

ipc() { omarchy-shell boundsj.raman "$@"; }
# Hyprland's Lua config takes dispatchers as Lua expressions.
goto_workspace() { hyprctl dispatch "hl.dsp.focus({ workspace = \"$1\" })" >/dev/null; }

# Closing a keyboard panel hands focus back to the last window, which can pull
# the view to that window's workspace. Re-focus the empty workspace before
# every capture and refuse to shoot if any window would be in frame.
ensure_empty() {
  local id windows
  for _ in 1 2 3; do
    goto_workspace "$workspace"
    sleep 0.4
    read -r id windows < <(hyprctl activeworkspace -j | jq -r '"\(.id) \(.windows)"')
    [[ $id == "$workspace" && $windows == 0 ]] && return 0
  done
  echo "workspace $workspace is not empty and focused; not capturing" >&2
  exit 1
}

# grim waits forever for a frame from a display that is asleep.
if hyprctl monitors -j | jq -e '.[] | select(.focused) | .dpmsStatus == false' >/dev/null; then
  echo "the display is asleep (DPMS off); wake it and run again" >&2
  exit 1
fi
grim_bin=$(type -P grim)
grim() { timeout 10 "$grim_bin" "$@"; }

previous=$(hyprctl activeworkspace -j | jq -r .id)
screen_w=$(hyprctl monitors -j | jq -r '.[] | select(.focused) | .width')
scale=$(hyprctl monitors -j | jq -r '.[] | select(.focused) | .scale')

restore() {
  ipc close >/dev/null 2>&1 || true
  ipc demo off >/dev/null 2>&1 || true
  goto_workspace "$previous"
  rm -rf "$tmp"
}
trap restore EXIT

ensure_empty

for scene in green yellow red; do
  ipc demo "$scene"
  sleep 2.5 # let the gauge and label animations settle

  # Bar item, padded a little horizontally (grim -g takes logical px).
  ensure_empty
  geo=$(ipc geometry)
  read -r x y w h < <(jq -r '"\(.x) \(.y) \(.w) \(.h)"' <<<"$geo")
  grim -g "$((x - 10)),$y $((w + 20))x$h" "$out/bar-$scene.png"

  ensure_empty
  grim "$tmp/closed.png"
  ipc open
  sleep 2
  if [[ $scene == red ]]; then
    # Arm (not confirm) the top row to show the two-step kill. Demo kills
    # never signal real processes anyway.
    wtype j && sleep 0.3 && wtype x && sleep 0.5
  fi
  read -r id windows < <(hyprctl activeworkspace -j | jq -r '"\(.id) \(.windows)"')
  if [[ $id != "$workspace" || $windows != 0 ]]; then
    echo "view left the empty workspace during $scene; not capturing" >&2
    exit 1
  fi
  grim "$tmp/open.png"
  ipc close
  sleep 0.5

  # Bounding box of what the panel changed, below the bar strip (physical px).
  top=$(awk -v h="$h" -v s="$scale" 'BEGIN { printf "%d", (h + 2) * s }')
  bbox=$(magick "$tmp/open.png" "$tmp/closed.png" -compose difference -composite \
    -crop "+0+$top" +repage -colorspace gray -threshold 4% -format '%@' info:)
  if [[ ! $bbox =~ ^([0-9]+)x([0-9]+)\+([0-9]+)\+([0-9]+)$ ]]; then
    echo "could not locate the panel for $scene ($bbox)" >&2
    exit 1
  fi
  bw=${BASH_REMATCH[1]} bh=${BASH_REMATCH[2]} bx=${BASH_REMATCH[3]} by=$((BASH_REMATCH[4] + top))
  if (( bw > screen_w / 2 )); then
    echo "panel diff for $scene is ${bw}px wide; something else changed on screen" >&2
    exit 1
  fi
  magick "$tmp/open.png" -crop "${bw}x${bh}+${bx}+${by}" +repage "$out/panel-$scene.png"
  echo "$scene: bar ${w}x${h}, panel ${bw}x${bh}"
done
