#!/bin/sh
# Richtet das Wand-Dashboard auf antiX ein. Kann beliebig oft laufen.
#   sh setup.sh              einrichten
#   sh setup.sh --entfernen  Autostart, Cron-Job und Firefox-Profil entfernen
set -eu

SEITE=$HOME/dashboard.html
UPDATE=$HOME/dashboard-update.py
PRIVAT=$HOME/dashboard-privat.conf
STARTUP=$HOME/.desktop-session/startup
PROFIL=$HOME/.dashboard-firefox

entferne_autostart() {
	if [ -f "$STARTUP" ]; then
		sed -i '/^# >>> dashboard$/,/^# <<< dashboard$/d' "$STARTUP"
	fi
}

# Filtert auch den Eintrag der früheren Version heraus
ohne_dashboard_jobs() {
	crontab -l 2>/dev/null | grep -v -E 'muell-update\.sh|dashboard-update\.py' || true
}

entferne_cron() {
	if command -v crontab >/dev/null 2>&1; then
		ohne_dashboard_jobs | crontab -
	fi
}

if [ "${1:-}" = "--entfernen" ]; then
	entferne_autostart
	entferne_cron
	rm -rf "$PROFIL"
	echo "Autostart, Cron-Job und Firefox-Profil entfernt."
	exit 0
fi

for datei in "$SEITE" "$UPDATE" "$PRIVAT"; do
	[ -f "$datei" ] || { echo "Nicht gefunden: $datei" >&2; exit 1; }
done

FIREFOX=$(command -v firefox-esr || command -v firefox || true)
[ -n "$FIREFOX" ] || { echo "Firefox nicht gefunden." >&2; exit 1; }

if ! python3 -c 'import dateutil' 2>/dev/null; then
	echo "Python 3 mit dateutil fehlt. Installieren: sudo apt install python3 python3-dateutil" >&2
	exit 1
fi

# Eigenes Profil: Nach Stromausfall soll die Seite erscheinen, nicht die Wiederherstellung
mkdir -p "$PROFIL"
cat > "$PROFIL/user.js" <<'EOF'
user_pref("browser.sessionstore.resume_from_crash", false);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("datareporting.policy.dataSubmissionEnabled", false);
EOF

# Eine eigene startup-Datei ersetzt die systemweite, darum mit deren Inhalt beginnen
mkdir -p "$(dirname "$STARTUP")"
if [ ! -f "$STARTUP" ]; then
	cp /etc/desktop-session/startup "$STARTUP" 2>/dev/null || : > "$STARTUP"
fi
entferne_autostart
{
	echo '# >>> dashboard'
	echo 'xset s off -dpms'
	echo "(sleep 8; \"$FIREFOX\" --kiosk --profile \"$PROFIL\" \"file://$SEITE\") &"
	echo '# <<< dashboard'
} >> "$STARTUP"
chmod u+x "$STARTUP"
echo "Autostart eingetragen: $STARTUP"

# Täglich um 3:17 Uhr, das Neuladen der Seite um 4:00 Uhr übernimmt die Daten
if command -v crontab >/dev/null 2>&1; then
	{ ohne_dashboard_jobs; echo "17 3 * * * python3 \"$UPDATE\" >/dev/null 2>&1"; } | crontab -
	echo "Cron-Job eingetragen."
else
	echo "WARNUNG: crontab fehlt, die Daten werden nicht automatisch aktualisiert." >&2
fi

rm -f "$HOME/muell-update.sh"
python3 "$UPDATE" "$SEITE" || echo "WARNUNG: Nicht alle Daten konnten geladen werden." >&2
