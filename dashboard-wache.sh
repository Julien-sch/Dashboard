#!/bin/sh
# Läuft jede Minute per Cron (eingerichtet von setup.sh):
#  - schaltet den Bildschirm nachts aus, sobald eine Weile niemand das Display berührt hat;
#    eine Berührung weckt ihn wieder
#  - startet den Kiosk-Firefox neu, falls er abgestürzt ist
# Für Wartung am Tablet pausieren:  touch ~/dashboard-pause   (danach wieder löschen)

AUS_VON=0000   # Bildschirm-Abschaltung ab (HHMM)
AUS_BIS=0600   # bis
AUS_NACH=120   # Sekunden ohne Berührung, bis der Bildschirm ausgeht

SEITE=$HOME/dashboard.html
PROFIL=$HOME/.dashboard-firefox
export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"

[ -e "$HOME/dashboard-pause" ] && exit 0
# Ohne laufende grafische Sitzung gibt es nichts zu tun
xset q >/dev/null 2>&1 || exit 0

# Bildschirm: nachts DPMS mit kurzer Frist einschalten, tagsüber ganz aus
jetzt=$(date +%H%M)
if [ "$AUS_VON" -le "$AUS_BIS" ]; then
	{ [ "$jetzt" -ge "$AUS_VON" ] && [ "$jetzt" -lt "$AUS_BIS" ]; } && nachts=1 || nachts=0
else
	{ [ "$jetzt" -ge "$AUS_VON" ] || [ "$jetzt" -lt "$AUS_BIS" ]; } && nachts=1 || nachts=0
fi
if xset q | grep -q "DPMS is Enabled"; then
	[ "$nachts" = 1 ] || { xset dpms force on; xset -dpms; }
else
	[ "$nachts" = 0 ] || xset +dpms dpms 0 0 "$AUS_NACH"
fi

# Firefox: läuft er noch?
pgrep -u "$(id -u)" -f -- "--profile $PROFIL" >/dev/null && exit 0
# Kurz nach dem Hochfahren startet ihn der Autostart selbst
[ "$(cut -d. -f1 /proc/uptime)" -ge 180 ] || exit 0
FIREFOX=$(command -v firefox-esr || command -v firefox) || exit 0
nohup "$FIREFOX" --kiosk --profile "$PROFIL" "file://$SEITE" >/dev/null 2>&1 &
