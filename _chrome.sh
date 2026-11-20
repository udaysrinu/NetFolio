#!/bin/bash
# Drive the user's REAL Google Chrome (not a Playwright test browser), per their standing preference.
#   _chrome.sh open  <url>              new Chrome window, ~1440x900 viewport, at <url>
#   _chrome.sh phone <path>             same, showing <path> inside a 390x844 frame (Chrome won't size a
#                                       window that narrow; media queries follow the frame's width)
#   _chrome.sh js    <url-part> <file>  run a JS file in the tab whose URL contains <url-part>; prints
#                                       the result (the JS must evaluate to a string - JSON.stringify)
#   _chrome.sh shot  <title-part> <out> screenshot ONLY that Chrome window, even if it is covered
#   _chrome.sh close <url-part>         close the tab(s) this script opened for <url-part>
# Needs Chrome > View > Developer > "Allow JavaScript from Apple Events", and Screen Recording for shots.
set -e
cmd=$1; shift
case "$cmd" in
  open|phone)
    url=$1; [ "$cmd" = phone ] && url="http://localhost:8899/__frame?w=390&h=844&u=$1"
    osascript -e "tell application \"Google Chrome\"
      set w to make new window
      set bounds of w to {0, 25, 1440, 1025}
      set URL of active tab of w to \"$url\"
    end tell" >/dev/null ;;
  js)
    part=$1; JS=$(python3 -c "import json,sys;print(json.dumps(open(sys.argv[1]).read()))" "$2")
    osascript -e "tell application \"Google Chrome\"
      repeat with w in windows
        repeat with t in tabs of w
          if URL of t contains \"$part\" then return execute t javascript $JS
        end repeat
      end repeat
      return \"NO TAB matching $part\"
    end tell" ;;
  shot)
    id=$(swift "$(dirname "$0")/_winid.swift" 2>/dev/null | awk -F'\t' -v p="$1" 'index($2,p){print $1; exit}')
    [ -n "$id" ] || { echo "no Chrome window titled like: $1" >&2; exit 1; }
    for i in 1 2 3; do screencapture -x -o -l "$id" "$2" 2>/dev/null && { echo "$2"; exit 0; }; sleep 1; done
    echo "capture failed for window $id" >&2; exit 1 ;;   # a just-opened window may not be composited yet
  close)   # backwards: closing a window's last tab removes the window and shifts every later index
    osascript -e "tell application \"Google Chrome\"
      repeat with i from (count of windows) to 1 by -1
        repeat with j from (count of tabs of window i) to 1 by -1
          if URL of tab j of window i contains \"$1\" then close tab j of window i
        end repeat
      end repeat
    end tell" >/dev/null ;;
  *) sed -n 2,12p "$0"; exit 2 ;;
esac
