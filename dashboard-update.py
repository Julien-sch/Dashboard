#!/usr/bin/env python3
"""Holt Mülltermine, Busabfahrten und Kalendertermine und schreibt sie in dashboard.html.

Der Browser darf diese Daten von file:// aus nicht selbst laden. Private Angaben stehen
in dashboard-privat.conf neben der HTML-Datei. Schlägt eine Quelle fehl oder fehlt ihre
Angabe, bleiben ihre bisherigen Daten in der Seite stehen.
"""
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


def schreibe_block(html, name, konstante, zeilen):
    block = "\n".join(json.dumps(z, ensure_ascii=False).replace("<", "\\u003c") + "," for z in zeilen)
    neu = f"/* {name}-START */\nconst {konstante} = [\n{block}\n];\n/* {name}-ENDE */"
    return re.sub(rf"/\* {name}-START \*/.*?/\* {name}-ENDE \*/", lambda _: neu, html, count=1, flags=re.S)


def main():
    seite = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "dashboard.html"
    privat = lies_privat(seite.with_name("dashboard-privat.conf"))
    html = seite.read_text(encoding="utf-8")
    fehlgeschlagen = False
    for name, konstante, quelle in (("MUELL", "MUELL_TERMINE", muell),
                                    ("BUS", "BUS_ABFAHRTEN", bus),
                                    ("KALENDER", "KALENDER_TERMINE", kalender)):
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
        print(f"{name}: {len(zeilen)} Einträge")
    neu = seite.with_suffix(".neu")
    neu.write_text(html, encoding="utf-8", newline="\n")
    shutil.copymode(seite, neu)
    os.replace(neu, seite)
    sys.exit(1 if fehlgeschlagen else 0)


if __name__ == "__main__":
    main()
