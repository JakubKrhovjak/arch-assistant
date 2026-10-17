#!/bin/bash
# Diagnose collector: the pending update list and recent Arch news titles.
set -uo pipefail
cat >/dev/null   # finding JSON not needed
echo "===== pending updates (repo)"
checkupdates 2>/dev/null | head -80
echo "===== pending updates (AUR)"
yay -Qua 2>/dev/null | head -40
echo "===== pending updates (Flatpak)"
flatpak remote-ls --updates --columns=application,version 2>/dev/null | head -40
echo "===== Arch news cache (titles)"
grep -o "<title>[^<]*</title>" "${AA_STATE_DIR:-.}/arch-news.xml" 2>/dev/null | head -8 | sed 's/<[^>]*>//g'
echo "===== running kernel / installed"
uname -r
pacman -Q linux linux-lts 2>/dev/null
