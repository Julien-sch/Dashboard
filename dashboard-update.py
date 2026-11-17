#!/usr/bin/env python3
"""Holt Mülltermine, Busabfahrten und Kalendertermine und schreibt sie in dashboard.html.

Der Browser darf diese Daten von file:// aus nicht selbst laden. Private Angaben stehen
in dashboard-privat.conf neben der HTML-Datei. Schlägt eine Quelle fehl oder fehlt ihre
Angabe, bleiben ihre bisherigen Daten in der Seite stehen.
"""
import argparse
import json
import os
import re
import shutil
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")
ALBA = "https://alba-bs.de/service/abfuhrtermine/ajax-kalender.html"
TRANSITOUS = "https://api.transitous.org/api/v5/stoptimes"
BUS_ANZAHL = 200       # reicht für gut zwei Tage, falls ein nächtlicher Abruf ausfällt
KALENDER_TAGE = 60
USER_AGENT = "wand-dashboard/2.0"


def lade(url, user_agent=USER_AGENT):
    anfrage = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(anfrage, timeout=30) as antwort:
        return antwort.read().decode("utf-8")


def lies_privat(pfad):
    werte = {}
    for zeile in pfad.read_text(encoding="utf-8").splitlines():
        if "=" in zeile and not zeile.lstrip().startswith("#"):
            name, wert = zeile.split("=", 1)
            werte[name.strip()] = wert.strip()
    return werte


def ics_ereignisse(text):
    """Liefert je VEVENT eine Liste aus (Name, Parameter, Wert)."""
    ereignisse, aktuell = [], None
    for zeile in re.sub(r"\r?\n[ \t]", "", text).splitlines():
        if zeile == "BEGIN:VEVENT":
            aktuell = []
        elif zeile == "END:VEVENT":
            ereignisse.append(aktuell)
            aktuell = None
        elif aktuell is not None and ":" in zeile:
            kopf, wert = zeile.split(":", 1)
            name, *parameter = kopf.split(";")
            aktuell.append((name, parameter, wert))
    return ereignisse


def feld(ereignis, name):
    return next((e for e in ereignis if e[0] == name), None)


def lokale_zeit(parameter, wert):
    """Wandelt einen ICS-Zeitpunkt in Berliner Ortszeit ohne Zeitzone um."""
    if "T" not in wert:
        return datetime.strptime(wert[:8], "%Y%m%d")
    zeit = datetime.strptime(wert[:15], "%Y%m%dT%H%M%S")
    if wert.endswith("Z"):
        zone = timezone.utc
    else:
        tzid = next((p[5:] for p in parameter if p.startswith("TZID=")), None)
        try:
            zone = ZoneInfo(tzid) if tzid else BERLIN
        except (KeyError, ValueError):
            zone = BERLIN
    return zeit.replace(tzinfo=zone).astimezone(BERLIN).replace(tzinfo=None)


def muell(privat):
    if not privat.get("MUELL_STRASSE"):
        return None
    p = "tx_mfabfallkalender_mfabfallkalender"
    jahr = date.today().year
    termine = set()
    for j in (jahr, jahr + 1):
        url = ALBA + "?" + urllib.parse.urlencode({
            f"{p}[action]": "makeical",
            f"{p}[controller]": "Abfallkalender",
            f"{p}[mf-trash-strasse]": privat["MUELL_STRASSE"],
            f"{p}[mf-trash-hausnr]": privat.get("MUELL_HAUSNR", ""),
            f"{p}[mf-trash-hausnrzusatz]": privat.get("MUELL_ZUSATZ", ""),
            f"{p}[mf-trash-month]": 1,
            f"{p}[mf-trash-thisyear]": j,
        })
        for ereignis in ics_ereignisse(lade(url)):
            tag = feld(ereignis, "DTSTART")[2]
            art = feld(ereignis, "SUMMARY")[2].removesuffix(" Abholung")
            termine.add(f"{tag[:4]}-{tag[4:6]}-{tag[6:8]} {art}")
    if not any(t.startswith(f"{jahr}-") for t in termine):
        raise ValueError(f"keine Termine für {jahr} erhalten")
    return sorted(termine)


def bus(privat):
    steig, kontakt = privat.get("BUS_STEIG"), privat.get("KONTAKT")
    if not steig or not kontakt:   # Transitous verlangt eine Kontaktangabe
        return None
    url = TRANSITOUS + "?" + urllib.parse.urlencode({"stopId": steig, "n": BUS_ANZAHL, "radius": 0})
    fahrten = json.loads(lade(url, f"{USER_AGENT} ({kontakt})"))["stopTimes"]
    abfahrten = [f"{f['place']['departure']} {f['routeShortName']} {f['headsign']}"
                 for f in fahrten if f["place"]["stopId"] == steig and not f.get("cancelled")]
    if not abfahrten:
        raise ValueError("keine Abfahrten erhalten")
    return sorted(abfahrten)


def wiederholungen(ereignis, start, bis):
    """Liefert alle Termine eines Ereignisses von heute bis zum Ende des Zeitfensters."""
    heute = datetime.combine(date.today(), datetime.min.time())
    regel = feld(ereignis, "RRULE")
    if not regel:
        return [start] if heute <= start <= bis else []
    from dateutil.rrule import rrulestr
    # UNTIL liegt oft in UTC vor, gerechnet wird aber in Ortszeit ohne Zeitzone
    text = re.sub(r"UNTIL=(\d{8})(T\d{6}Z?)?", r"UNTIL=\g<1>T235959", regel[2])
    return list(rrulestr(text, dtstart=start).between(heute, bis, inc=True))


def kalender(privat):
    urls = privat.get("KALENDER_URLS", "").split()
    if not urls:
        return None
    bis = datetime.combine(date.today() + timedelta(days=KALENDER_TAGE), datetime.min.time())
    termine = []
    for url in urls:
        ereignisse = ics_ereignisse(lade(url.replace("webcal://", "https://", 1)))
        # Einzeln verschobene oder gelöschte Vorkommen gehören nicht zur Serie
        ausnahmen = {}
        for e in ereignisse:
            for name, parameter, wert in e:
                if name in ("EXDATE", "RECURRENCE-ID"):
                    uid = feld(e, "UID")[2] if feld(e, "UID") else ""
                    for w in wert.split(","):
                        ausnahmen.setdefault(uid, set()).add(lokale_zeit(parameter, w))
        for e in ereignisse:
            start_feld, titel_feld = feld(e, "DTSTART"), feld(e, "SUMMARY")
            if not start_feld or not titel_feld:
                continue
            uid = feld(e, "UID")[2] if feld(e, "UID") else ""
            ganztags = "T" not in start_feld[2]
            titel = re.sub(r"\\([,;\\])", r"\1", titel_feld[2]).replace("\\n", " ")
            start = lokale_zeit(start_feld[1], start_feld[2])
            try:
                vorkommen = wiederholungen(e, start, bis)
            except ValueError as fehler:
                print(f"Termin übersprungen ({titel}): {fehler}", file=sys.stderr)
                continue
            ist_serie = feld(e, "RRULE") is not None
            for zeit in vorkommen:
                if ist_serie and zeit in ausnahmen.get(uid, ()):
                    continue
                termine.append(zeit.strftime("%Y-%m-%d" if ganztags else "%Y-%m-%dT%H:%M") + " " + titel)
    return sorted(termine)


def block_muster(name):
    return re.compile(rf"/\* {name}-START \*/.*?/\* {name}-ENDE \*/", re.S)


def ersetze_block(html, name, inhalt):
    return block_muster(name).sub(lambda _: f"/* {name}-START */\n{inhalt}\n/* {name}-ENDE */", html, count=1)


def schreibe_block(html, name, konstante, zeilen):
    block = "\n".join(json.dumps(z, ensure_ascii=False).replace("<", "\\u003c") + "," for z in zeilen)
    return ersetze_block(html, name, f"const {konstante} = [\n{block}\n];")


QUELLEN = (("MUELL", "MUELL_TERMINE", muell),
           ("BUS", "BUS_ABFAHRTEN", bus),
           ("KALENDER", "KALENDER_TERMINE", kalender))


def lies_erfolg(html):
    """Tag des letzten erfolgreichen Abrufs je Quelle, z. B. {"BUS": "2026-11-17"}."""
    treffer = re.search(r"const DATEN_ERFOLG = (\{.*?\});", html, re.S)
    try:
        return json.loads(treffer.group(1)) if treffer else {}
    except ValueError:
        return {}


def uebernimm_daten(alt, neu):
    """Trägt die Daten einer bisherigen Seite in eine neue Fassung der Seite ein."""
    for name in [q[0] for q in QUELLEN] + ["ERFOLG"]:
        treffer = block_muster(name).search(alt)
        if treffer and block_muster(name).search(neu):
            neu = block_muster(name).sub(lambda _: treffer.group(0), neu, count=1)
    return neu


def schreibe_datei(pfad, text, vorbild=None):
    """Schreibt erst eine Kopie und tauscht sie dann aus, damit nie eine halbe Datei liegt."""
    neu = pfad.with_name(pfad.name + ".neu")
    neu.write_text(text, encoding="utf-8", newline="\n")
    if vorbild:
        shutil.copymode(vorbild, neu)
    os.replace(neu, pfad)


def main():
    argumente = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    argumente.add_argument("seite", nargs="?", type=Path, default=Path.home() / "dashboard.html")
    argumente.add_argument("--nur", nargs="+", metavar="QUELLE",
                           choices=[name.lower() for name, _, _ in QUELLEN],
                           help="nur diese Quellen abrufen, z. B. --nur kalender")
    argumente.add_argument("--nachholen", action="store_true",
                           help="den Kalender immer abrufen, die übrigen Quellen nur, "
                                "wenn sie heute noch nicht erfolgreich waren")
    argumente.add_argument("--daten-aus", type=Path, metavar="ALTE_SEITE",
                           help="vorher die Daten einer bisherigen Seite übernehmen (nach einem Update)")
    optionen = argumente.parse_args()
    seite = optionen.seite
    privat = lies_privat(seite.with_name("dashboard-privat.conf"))
    alt = html = seite.read_text(encoding="utf-8")
    if optionen.daten_aus and optionen.daten_aus.exists():
        html = uebernimm_daten(optionen.daten_aus.read_text(encoding="utf-8"), html)
    erfolg = lies_erfolg(html)
    heute = date.today().isoformat()
    fehlgeschlagen = False
    for name, konstante, quelle in QUELLEN:
        if optionen.nur and name.lower() not in optionen.nur:
            continue
        if optionen.nachholen and name != "KALENDER" and erfolg.get(name) == heute:
            continue
        if f"/* {name}-START */" not in html:
            print(f"{name}: Block fehlt in {seite}", file=sys.stderr)
            fehlgeschlagen = True
            continue
        try:
            zeilen = quelle(privat)
        except Exception as fehler:
            print(f"{name}: {fehler}, bisherige Daten bleiben", file=sys.stderr)
            fehlgeschlagen = True
            continue
        if zeilen is None:
            print(f"{name}: nicht eingerichtet, übersprungen")
            continue
        html = schreibe_block(html, name, konstante, zeilen)
        erfolg[name] = heute
        print(f"{name}: {len(zeilen)} Einträge")
    # Nur das Datum, damit sich die Seite durch den stündlichen Abruf nicht stündlich neu lädt
    html = ersetze_block(html, "ERFOLG", f"const DATEN_ERFOLG = {json.dumps(erfolg, sort_keys=True)};")
    if html == alt:
        print("Keine Änderung, Seite bleibt unverändert")
        sys.exit(1 if fehlgeschlagen else 0)
    # Die offene Seite vergleicht diesen Stand mit ihrem eigenen und lädt sich bei Abweichung neu
    stand = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    if block_muster("STAND").search(html):
        html = ersetze_block(html, "STAND", f'const DATEN_STAND = "{stand}";')
    else:
        print(f"STAND: Block fehlt in {seite}, die Seite übernimmt neue Daten erst beim Neuladen",
              file=sys.stderr)
    schreibe_datei(seite, html, vorbild=seite)
    # Erst nach der Seite schreiben, sonst lädt sie sich womöglich neu, bevor die Daten drinstehen
    schreibe_datei(seite.with_name("dashboard-stand.js"), f'datenStand("{stand}");\n')
    sys.exit(1 if fehlgeschlagen else 0)


if __name__ == "__main__":
    main()
