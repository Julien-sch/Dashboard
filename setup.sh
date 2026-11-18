#!/bin/sh
# Richtet das Wand-Dashboard auf antiX ein. Kann beliebig oft laufen.
#   sh setup.sh              einrichten bzw. nach einem Update übernehmen
#   sh setup.sh --entfernen  Autostart, Cron-Jobs und Firefox-Profil entfernen
#
# Liegt dieses Skript in einem geklonten Repo (z. B. ~/Dashboard), kopiert es die Dateien
# nach $HOME. Die Daten der bisherigen Seite bleiben dabei erhalten. Update also:
#   cd ~/Dashboard && git pull && sh setup.sh
set -eu

QUELLE=$(cd "$(dirname "$0")" && pwd)
SEITE=$HOME/dashboard.html
UPDATE=$HOME/dashboard-update.py
WACHE=$HOME/dashboard-wache.sh
BEFEHL=$HOME/dashboard-befehl.py
PRIVAT=$HOME/dashboard-privat.conf
STARTUP=$HOME/.desktop-session/startup
PROFIL=$HOME/.dashboard-firefox
# Neustart gegen hängendes WLAN, täglich um 11:30 und 23:30 (Minute Stunden, Cron-Format).
# Braucht Root, darum ein eigener Eintrag in /etc/cron.d statt in der Crontab des Nutzers.
NEUSTART=/etc/cron.d/dashboard-neustart
NEUSTART_ZEIT="30 11,23"

neustart_job() {
	echo "# Von setup.sh des Dashboards angelegt. Entfernen: sh setup.sh --entfernen"
	echo "$NEUSTART_ZEIT * * * root [ -e \"$HOME/dashboard-pause\" ] || /sbin/shutdown -r now"
}
# Erlaubt dem Sprachbefehl "Neustart" (dashboard-befehl.py) genau diesen einen Befehl ohne Passwort
SUDOERS=/etc/sudoers.d/dashboard

entferne_autostart() {
	if [ -f "$STARTUP" ]; then
		sed -i '/^# >>> dashboard$/,/^# <<< dashboard$/d' "$STARTUP"
	fi
}

# Filtert auch den Eintrag der früheren Version heraus
ohne_dashboard_jobs() {
	crontab -l 2>/dev/null | grep -v -E 'muell-update\.sh|dashboard-update\.py|dashboard-wache\.sh' || true
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
	if [ -f "$NEUSTART" ] || [ -f "$SUDOERS" ]; then
		sudo rm -f "$NEUSTART" "$SUDOERS" || echo "WARNUNG: $NEUSTART und $SUDOERS bitte selbst mit sudo löschen." >&2
	fi
	echo "Autostart, Cron-Jobs, geplanter Neustart und Firefox-Profil entfernt."
	exit 0
fi

[ -f "$PRIVAT" ] || { echo "Nicht gefunden: $PRIVAT" >&2; exit 1; }

FIREFOX=$(command -v firefox-esr || command -v firefox || true)
[ -n "$FIREFOX" ] || { echo "Firefox nicht gefunden." >&2; exit 1; }

if ! python3 -c 'import dateutil' 2>/dev/null; then
	echo "Python 3 mit dateutil fehlt. Installieren: sudo apt install python3 python3-dateutil" >&2
	exit 1
fi

# Aus einem Repo-Ordner die Dateien nach $HOME holen, die Daten der alten Seite merken
DATEN_AUS=""
if [ "$QUELLE" != "$HOME" ]; then
	for datei in dashboard.html dashboard-update.py dashboard-wache.sh dashboard-befehl.py; do
		[ -f "$QUELLE/$datei" ] || { echo "Nicht gefunden: $QUELLE/$datei" >&2; exit 1; }
	done
	if [ -f "$SEITE" ]; then
		cp "$SEITE" "$SEITE.alt"
		DATEN_AUS="--daten-aus=$SEITE.alt"
	fi
	cp "$QUELLE/dashboard.html" "$SEITE"
	cp "$QUELLE/dashboard-update.py" "$UPDATE"
	cp "$QUELLE/dashboard-wache.sh" "$WACHE"
	cp "$QUELLE/dashboard-befehl.py" "$BEFEHL"
	echo "Dateien aus $QUELLE übernommen."
fi

for datei in "$SEITE" "$UPDATE" "$WACHE" "$BEFEHL"; do
	[ -f "$datei" ] || { echo "Nicht gefunden: $datei" >&2; exit 1; }
done

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

# Stündlich um :47 den Kalender; Müll und Bus einmal am Tag (kurz nach Mitternacht),
# bei einem Fehlschlag stündlich erneut. Dazu einmal kurz nach dem Hochfahren.
# Die offene Seite merkt neue Daten an dashboard-stand.js und lädt sich dann selbst neu.
# Jede Minute prüft dashboard-wache.sh Firefox und die nächtliche Bildschirmabschaltung.
if command -v crontab >/dev/null 2>&1; then
	{
		ohne_dashboard_jobs
		echo "@reboot sleep 60 && python3 \"$UPDATE\" --nachholen >/dev/null 2>&1"
		echo "47 * * * * python3 \"$UPDATE\" --nachholen >/dev/null 2>&1"
		echo "* * * * * sh \"$WACHE\" >/dev/null 2>&1"
	} | crontab -
	echo "Cron-Jobs eingetragen."
else
	echo "WARNUNG: crontab fehlt, die Daten werden nicht automatisch aktualisiert." >&2
fi

# Geplanter Neustart; nach sudo wird nur gefragt, wenn sich der Eintrag geändert hat
if neustart_job | cmp -s - "$NEUSTART" 2>/dev/null; then
	echo "Geplanter Neustart ist eingerichtet (11:30 und 23:30)."
elif neustart_job | sudo tee "$NEUSTART" >/dev/null && sudo chmod 644 "$NEUSTART"; then
	echo "Geplanter Neustart eingetragen: $NEUSTART (11:30 und 23:30)."
else
	echo "WARNUNG: Geplanter Neustart konnte nicht eingetragen werden (sudo nötig)." >&2
fi

# Sprachbefehle per Siri: Neustart ohne Passwort erlauben (die Regel wird vor dem Eintragen geprüft)
# Auf die Datei prüfen, nicht mit sudo -n: ein kurz vorher eingegebenes sudo-Passwort täuscht sonst
if [ -f "$SUDOERS" ]; then
	echo "Neustart per Sprachbefehl ist erlaubt."
else
	regel=$(mktemp)
	echo "$(id -un) ALL=(root) NOPASSWD: /sbin/shutdown -r now" > "$regel"
	if sudo visudo -cf "$regel" >/dev/null && sudo install -m 440 -o root -g root "$regel" "$SUDOERS"; then
		echo "Neustart per Sprachbefehl erlaubt: $SUDOERS"
	else
		echo "WARNUNG: Neustart per Sprachbefehl konnte nicht erlaubt werden (sudo nötig)." >&2
	fi
	rm -f "$regel"
fi
[ -x /usr/sbin/sshd ] || echo "HINWEIS: Für die Siri-Befehle fehlt der SSH-Server: sudo apt install openssh-server"

rm -f "$HOME/muell-update.sh"
python3 "$UPDATE" "$SEITE" $DATEN_AUS || echo "WARNUNG: Nicht alle Daten konnten geladen werden." >&2
rm -f "$SEITE.alt"
