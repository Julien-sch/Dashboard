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

### Heizung (HAMA-Thermostate)

Unter der Uhr stehen je Raum Ist- und Soll-Temperatur (orange), Antippen öffnet die Seite „Zuhause“ mit allen Räumen.
`dashboard-heizung.py` fragt die Thermostate alle 5 Minuten direkt über die HAMA-Zentrale im WLAN ab, ohne Cloud.
Werte älter als 20 Minuten erscheinen blass. Wie viele Räume unter der Uhr stehen: `heizungAnzahl` in `dashboard.html`.

**Einmalig am PC:** Zentrale und Thermostate in der App *Smart Life* einrichten, auf platform.tuya.com ein
Cloud-Projekt (Central Europe) anlegen und das Smart-Life-Konto per QR-Code verknüpfen, dann:

```sh
pip install --user tinytuya
python -m tinytuya wizard     # Access ID, Access Secret, ID der Zentrale, Region eu
```

Die erzeugte `devices.json` enthält die Schlüssel. Sie kommt nur aufs Tablet, nicht ins Repo:

```sh
scp devices.json BENUTZER@TABLET-IP:dashboard-tuya.json
```

**Auf dem Tablet:**

```sh
sudo apt install python3-pip python3-cryptography python3-requests python3-colorama
pip3 install --user --no-deps --break-system-packages tinytuya   # ältere pip: ohne --break-system-packages
python3 ~/dashboard-heizung.py                                   # Test, zeigt je Raum Ist → Soll
```

Der Zentrale im Router eine feste IP geben. Ändert sie sich doch, die IP in `~/dashboard-tuya.json` anpassen.
Wird die Zentrale neu gekoppelt, ändert sich ihr Schlüssel: Wizard erneut ausführen und die Datei neu kopieren.
Neue Thermostate oder umbenannte Räume brauchen ebenfalls eine neue `devices.json`.

### Sprachbefehle mit Siri

Das iPhone schickt über die Kurzbefehle-App einen Befehl per SSH ans Tablet, `dashboard-befehl.py`
antwortet mit einem Satz, den Siri vorliest. Das iPhone muss dazu im selben WLAN sein.

**Auf dem Tablet, einmalig:**

```sh
sudo apt install openssh-server
hostname -I        # IP-Adresse des Tablets, am besten im Router fest vergeben
```

**Auf dem iPhone, je Befehl ein Kurzbefehl:** Der Name des Kurzbefehls ist der Satz, den du zu Siri sagst.

1. Aktion **„Skript über SSH ausführen“**: Host = IP des Tablets, Port 22, Benutzer und Passwort des Tablets.
   Sicherer statt Passwort: Authentifizierung „SSH-Schlüssel“, den öffentlichen Schlüssel teilen und auf dem
   Tablet an `~/.ssh/authorized_keys` anhängen.
2. Skript, z. B. `python3 ~/dashboard-befehl.py muell`
3. Aktion **„Ergebnis anzeigen“** mit dem Ergebnis des Skripts, Siri liest es dann vor.

| Kurzbefehl (Beispiel)     | Skript                                          |
|---------------------------|-------------------------------------------------|
| Dashboard aktualisieren   | `python3 ~/dashboard-befehl.py aktualisieren`   |
| Dashboard neu starten     | `python3 ~/dashboard-befehl.py neustart`        |
| Bildschirm aus            | `python3 ~/dashboard-befehl.py bildschirm aus`  |
| Bildschirm an             | `python3 ~/dashboard-befehl.py bildschirm an`   |
| Wann kommt der Müll       | `python3 ~/dashboard-befehl.py muell`           |
| Wann fährt der Bus        | `python3 ~/dashboard-befehl.py bus`             |
| Was steht heute an        | `python3 ~/dashboard-befehl.py termine`         |
| Was kostet Diesel         | `python3 ~/dashboard-befehl.py sprit`           |
| Wie steht es              | `python3 ~/dashboard-befehl.py sport`           |
| Bundesliga                | `python3 ~/dashboard-befehl.py sport bundesliga` |
| Zweite Liga               | `python3 ~/dashboard-befehl.py sport zweite liga` |
| NFL                       | `python3 ~/dashboard-befehl.py sport nfl`       |
| Wie spielt Werder         | `python3 ~/dashboard-befehl.py sport werder`    |

**Sport:** `sport` allein sagt, was in Bundesliga, 2. Bundesliga und NFL gerade läuft oder eben zu Ende ging.
Mit Liga kommt der ganze Spieltag, mit einem Teamnamen (z. B. `braunschweig`, `bvb`, `chiefs`) Stand,
nächstes und letztes Spiel des Teams. Für ein frei gesagtes Team: vor die SSH-Aktion **„Nach Eingabe fragen“**
setzen („Welches Team?“), die Antwort als *Eingabe* der SSH-Aktion übergeben und als Skript
`python3 ~/dashboard-befehl.py sport` nehmen. Das Dashboard blendet die Ergebnisse jeweils 60 Sekunden ein
(`sportSekunden` in `dashboard.html`), Antippen schließt die Einblendung. Daten: OpenLigaDB und ESPN (inoffiziell).

**Bildschirm aus** gilt bis „Bildschirm an“ oder bis 6 Uhr am nächsten Morgen, Antippen weckt ihn kurz.

## Aktualisieren

```sh
cd ~/Dashboard && git pull && sh setup.sh
```

Die bisherigen Daten bleiben dabei erhalten, die offene Seite lädt sich danach von selbst neu.

## Was automatisch läuft

- **Stündlich um :47:** Kalender und Spritpreise abrufen. Müll und Bus einmal am Tag, bei einem Fehlschlag stündlich erneut.
- **Nach dem Hochfahren:** fehlende Daten sofort nachholen.
- **Täglich um 11:30 und 23:30:** Neustart des Tablets, falls das WLAN hängt (`/etc/cron.d/dashboard-neustart`,
  `setup.sh` fragt dafür einmal nach dem sudo-Passwort). Fällt aus, solange `~/dashboard-pause` existiert.
  Damit auch der Sprachbefehl neu starten darf, legt `setup.sh` `/etc/sudoers.d/dashboard` an (nur `shutdown -r now`).
- **Jede Minute** (`dashboard-wache.sh`): Firefox nach einem Absturz neu starten, Bildschirm von 0 bis 6 Uhr
  nach 2 Minuten ohne Berührung abschalten. Pausieren für Wartung: `touch ~/dashboard-pause`, danach wieder löschen.
- **Jede Minute** (`/etc/cron.d/dashboard-netz`): Notfall-Routen löschen, die connman bei langsamem DHCP anlegt
  (`default dev wlan0 scope link`, Adresse `169.254.…`). Sie blockieren sonst das Internet per IPv4
  („No route to host“ bei Müll und Spritpreisen). Gelöscht wird nur, wenn die richtige Route `default via …` da ist.
- **Alle 5 Minuten** (`dashboard-heizung.py`): Thermostate abfragen, falls `~/dashboard-tuya.json` existiert.
- Schlägt ein Abruf länger als einen Tag fehl, steht unten links „Daten veraltet“.
