# Dashboard

Das ist ein Dashboard für ein altes Convertible Laptop mit 2gb RAM wo Antilx installiert ist.

## Einrichten auf dem Tablet

```sh
sudo apt install git python3 python3-dateutil
git clone https://github.com/Julien-sch/Dashboard.git ~/Dashboard
# ~/dashboard-privat.conf anlegen (Adresse, Bus-Haltestelle, Kalender-Link, Kontakt)
sh ~/Dashboard/setup.sh
```

`setup.sh` kopiert Seite und Skripte nach `$HOME`, richtet den Kiosk-Autostart und die Cron-Jobs ein.

### Spritpreise

Kostenlosen API-Schlüssel bei https://onboarding.tankerkoenig.de beantragen und in `~/dashboard-privat.conf` eintragen:

```
TANKERKOENIG_KEY=dein-schluessel
TANK_RADIUS=5
```

Die Spritsorte (`sprit: "e10"`, `"e5"` oder `"diesel"`) steht in der Konfiguration oben in `dashboard.html`.

## Aktualisieren

```sh
cd ~/Dashboard && git pull && sh setup.sh
```

Die bisherigen Daten bleiben dabei erhalten, die offene Seite lädt sich danach von selbst neu.

## Was automatisch läuft

- **Stündlich um :47:** Kalender und Spritpreise abrufen. Müll und Bus einmal am Tag, bei einem Fehlschlag stündlich erneut.
- **Nach dem Hochfahren:** fehlende Daten sofort nachholen.
- **Jede Minute** (`dashboard-wache.sh`): Firefox nach einem Absturz neu starten, Bildschirm von 0 bis 6 Uhr
  nach 2 Minuten ohne Berührung abschalten. Pausieren für Wartung: `touch ~/dashboard-pause`, danach wieder löschen.
- Schlägt ein Abruf länger als einen Tag fehl, steht unten links „Daten veraltet“.
