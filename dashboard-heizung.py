#!/usr/bin/env python3
"""Liest die Heizkörperthermostate über die HAMA-Zentrale im Heimnetz und schreibt dashboard-heizung.js.

Die Schlüssel stehen in ~/dashboard-tuya.json, das ist die devices.json aus
"python3 -m tinytuya wizard". Die Zentrale wird direkt im WLAN gefragt, ohne Cloud.
Die Seite lädt dashboard-heizung.js jede Minute, die Datei ruft heizungDaten() auf.
Ein Thermostat, das gerade nicht antwortet, behält seine letzten Werte samt altem Stand.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

# Datenpunkte der HAMA-Thermostate (176592/176661), Temperaturen in Zehntelgrad.
# Die Cloud nennt nur 101 und 103, 102 und 3 liefert die Zentrale zusätzlich.
DP_ZUSTAND, DP_AN, DP_IST, DP_SOLL = "3", "101", "102", "103"
VERSUCHE = 3


def lies_alt(pfad):
    """Die Daten des letzten Laufs, um bei einem Aussetzer die alten Werte zu behalten."""
    try:
        text = pfad.read_text(encoding="utf-8")
        return json.loads(text[text.index("(") + 1:text.rindex(")")])
    except (OSError, ValueError):
        return {}


def zehntel(wert):
    return wert / 10 if isinstance(wert, (int, float)) and not isinstance(wert, bool) else None


def verbinde(eintrag):
    import tinytuya
    # Ohne IP sucht tinytuya die Zentrale im Netz, das dauert. Besser: feste IP im Router.
    # Wenige, kurze Verbindungsversuche: Ist die Zentrale weg, soll der Lauf schnell enden
    zentrale = tinytuya.Device(eintrag["id"], eintrag.get("ip") or "Auto", eintrag["key"],
                               version=float(eintrag.get("version") or 3.3), persist=True,
                               connection_timeout=5, connection_retry_limit=2, connection_retry_delay=2)
    return zentrale


class ZentraleWeg(Exception):
    """Die Zentrale selbst antwortet nicht, die übrigen Thermostate dahinter brauchen keinen Versuch."""


def frage_ab(thermostat, zentrale):
    import tinytuya
    geraet = tinytuya.Device(thermostat["id"], cid=thermostat["node_id"], parent=zentrale)
    antwort = None
    for versuch in range(VERSUCHE):
        if versuch:
            time.sleep(2)
        antwort = geraet.status()
        dps = (antwort or {}).get("dps")
        if dps and DP_IST in dps:
            return dps
        if str((antwort or {}).get("Error", "")).startswith("Network Error"):
            raise ZentraleWeg(antwort["Error"])
    raise ValueError((antwort or {}).get("Error") or "keine Antwort")


def schreibe_datei(pfad, text):
    """Schreibt erst eine Kopie und tauscht sie dann aus, damit nie eine halbe Datei liegt."""
    neu = pfad.with_name(pfad.name + ".neu")
    neu.write_text(text, encoding="utf-8", newline="\n")
    os.replace(neu, pfad)


def main():
    argumente = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    argumente.add_argument("--geraete", type=Path, default=Path.home() / "dashboard-tuya.json",
                           help="devices.json aus dem tinytuya-Wizard")
    argumente.add_argument("--ziel", type=Path, default=Path.home() / "dashboard-heizung.js")
    optionen = argumente.parse_args()

    if not optionen.geraete.exists():
        print(f"Heizung nicht eingerichtet: {optionen.geraete} fehlt")
        return 0
    try:
        import tinytuya  # noqa: F401
    except ImportError:
        print("tinytuya fehlt, siehe README (Abschnitt Heizung)", file=sys.stderr)
        return 1

    geraete = json.loads(optionen.geraete.read_text(encoding="utf-8"))
    nach_id = {g["id"]: g for g in geraete}
    thermostate = [g for g in geraete if g.get("category") == "wk" and g.get("node_id") and g.get("parent")]
    if not thermostate:
        print(f"Keine Thermostate in {optionen.geraete}", file=sys.stderr)
        return 1

    alt = {r["id"]: r for r in lies_alt(optionen.ziel).get("raeume", [])}
    zentralen, weg, raeume = {}, set(), []
    neu = fehler = False
    for t in sorted(thermostate, key=lambda g: g["name"].lower()):
        try:
            if t["parent"] in weg:
                raise ValueError("Zentrale nicht erreichbar")
            if t["parent"] not in zentralen:
                if t["parent"] not in nach_id:
                    raise ValueError("Zentrale fehlt in der Geräteliste")
                zentralen[t["parent"]] = verbinde(nach_id[t["parent"]])
            dps = frage_ab(t, zentralen[t["parent"]])
            raeume.append({
                "id": t["id"], "name": t["name"],
                "ist": zehntel(dps.get(DP_IST)), "soll": zehntel(dps.get(DP_SOLL)),
                "an": dps.get(DP_AN) is not False, "zustand": dps.get(DP_ZUSTAND),
                "stand": int(time.time() * 1000),
            })
            neu = True
        except Exception as e:
            if isinstance(e, ZentraleWeg):
                weg.add(t["parent"])
            print(f"{t['name']}: {e}", file=sys.stderr)
            fehler = True
            if t["id"] in alt:
                raeume.append({**alt[t["id"]], "name": t["name"]})
    for zentrale in zentralen.values():
        zentrale.close()

    if neu:
        daten = {"zeit": int(time.time() * 1000), "raeume": raeume}
        inhalt = json.dumps(daten, ensure_ascii=False).replace("<", "\\u003c")
        schreibe_datei(optionen.ziel, f"heizungDaten({inhalt});\n")
        for r in raeume:
            print(f"{r['name']}: {r['ist']}° → {r['soll']}°")
    return 1 if fehler else 0


if __name__ == "__main__":
    sys.exit(main())
