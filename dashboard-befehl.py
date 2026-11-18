#!/usr/bin/env python3
"""Sprachbefehle für das Dashboard, gedacht für Siri-Kurzbefehle über SSH.

Gibt einen kurzen Satz aus, den der Kurzbefehl vorliest:
  python3 ~/dashboard-befehl.py muell
  python3 ~/dashboard-befehl.py sport werder
  echo "zweite Liga" | python3 ~/dashboard-befehl.py sport

Befehle: aktualisieren, neustart, bildschirm aus, bildschirm an, muell, bus, termine, sprit,
sport [Liga oder Team]. Bei sport blendet das Dashboard die Ergebnisse außerdem kurz ein.
"""
import importlib.util
import json
import os
import re
import select
import shlex
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ORDNER = Path(__file__).resolve().parent
SEITE = ORDNER / "dashboard.html"
_spec = importlib.util.spec_from_file_location("dashboard_update", ORDNER / "dashboard-update.py")
du = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(du)

BERLIN = du.BERLIN
OPENLIGADB = "https://api.openligadb.de/getmatchdata/"
ESPN_NFL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"   # inoffiziell, ohne Schlüssel
SHUTDOWN = ["/sbin/shutdown", "-r", "now"]   # so auch in /etc/sudoers.d/dashboard (setup.sh)
AUS_DATEI = Path.home() / "dashboard-bildschirm-aus"   # wertet dashboard-wache.sh aus
AUS_NACH = 120   # Sekunden ohne Berührung bis der Bildschirm wieder ausgeht, wie in dashboard-wache.sh
WOCHENTAGE = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
MONATE = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September",
          "Oktober", "November", "Dezember")


# ---------- Hilfen für Text und Zeit ----------

def woerter(text):
    """Kleinbuchstaben ohne Umlaute und Satzzeichen: "1. FC Köln" -> ["1", "fc", "koeln"]."""
    text = text.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.findall(r"[a-z0-9]+", text)


def aufzaehlung(teile):
    teile = list(teile)
    return teile[0] if len(teile) == 1 else ", ".join(teile[:-1]) + " und " + teile[-1]


def gross(text):
    return text[:1].upper() + text[1:]


def jetzt():
    return datetime.now(BERLIN)


def zeitpunkt(text):
    zeit = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return zeit if zeit.tzinfo else zeit.replace(tzinfo=timezone.utc)


def tag_text(tag):
    abstand = (tag - jetzt().date()).days
    namen = {-1: "gestern", 0: "heute", 1: "morgen", 2: "übermorgen"}
    if abstand in namen:
        return namen[abstand]
    if 0 < abstand < 7:
        return "am " + WOCHENTAGE[tag.weekday()]
    return f"am {WOCHENTAGE[tag.weekday()]}, {tag.day}. {MONATE[tag.month - 1]}"


def uhrzeit_text(zeit):
    """ "20 Uhr 30" statt "20:30", das liest Siri zuverlässig richtig vor."""
    return f"{zeit.hour} Uhr" + (f" {zeit.minute}" if zeit.minute else "")


def wann(zeit):
    zeit = zeit.astimezone(BERLIN)
    return f"{tag_text(zeit.date())} um {uhrzeit_text(zeit)}"


def kurz_wann(zeit):
    """Für die Anzeige, z. B. "Sa 15:30" oder "Heute 20:30"."""
    zeit = zeit.astimezone(BERLIN)
    tag = "Heute" if zeit.date() == jetzt().date() else WOCHENTAGE[zeit.weekday()][:2]
    return f"{tag} {zeit:%H:%M}"


def seite():
    return SEITE.read_text(encoding="utf-8")


def xset(*argumente):
    umgebung = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0"),
                "XAUTHORITY": os.environ.get("XAUTHORITY", str(Path.home() / ".Xauthority"))}
    return subprocess.run(["xset", *argumente], env=umgebung, capture_output=True, text=True,
                          timeout=10, check=True).stdout


def wecke_bildschirm():
    """Schaltet einen nachts abgeschalteten Bildschirm ein; die Wache schaltet ihn später wieder ab."""
    try:
        status = xset("q")
        if "Monitor is" in status and "Monitor is On" not in status:
            xset("dpms", "force", "on")
    except (OSError, subprocess.SubprocessError):
        pass


# ---------- Befehle rund ums Tablet ----------

QUELL_NAMEN = {"MUELL": "Müll", "BUS": "Bus", "KALENDER": "Kalender", "TANKEN": "Spritpreise"}


def aktualisieren(_):
    lauf = subprocess.run([sys.executable, str(ORDNER / "dashboard-update.py"), str(SEITE)],
                          capture_output=True, text=True, timeout=300)
    fehler = [QUELL_NAMEN[z.split(":")[0]] for z in lauf.stderr.splitlines() if z.split(":")[0] in QUELL_NAMEN]
    if lauf.returncode == 0:
        return "Das Dashboard ist aktualisiert."
    if fehler:
        return f"Aktualisiert, aber {aufzaehlung(fehler)} {'hat' if len(fehler) == 1 else 'haben'} nicht geklappt."
    return "Die Aktualisierung hat nicht geklappt."


def neustart(_):
    if subprocess.run(["sudo", "-n", "-l", *SHUTDOWN], capture_output=True).returncode != 0:
        return "Der Neustart ist nicht erlaubt. Bitte setup.sh auf dem Tablet noch einmal ausführen."
    # Verzögert und abgekoppelt, damit die Antwort noch beim iPhone ankommt
    subprocess.Popen(["sh", "-c", "sleep 5; exec sudo -n " + " ".join(SHUTDOWN)], start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return "Das Dashboard startet jetzt neu."


def bildschirm(rest):
    aus = bool({"aus", "ab", "off"} & set(rest))
    if aus == bool({"an", "ein", "on"} & set(rest)):
        return "Sag Bildschirm an oder Bildschirm aus."
    try:
        if aus:
            xset("+dpms", "dpms", "0", "0", str(AUS_NACH))
            xset("dpms", "force", "off")
            # Gilt bis "Bildschirm an" oder bis zum Ende der nächsten Nacht, siehe dashboard-wache.sh
            AUS_DATEI.touch()
            return "Der Bildschirm ist aus. Antippen weckt ihn kurz."
        AUS_DATEI.unlink(missing_ok=True)
        xset("dpms", "force", "on")
        xset("-dpms")
        return "Der Bildschirm ist an."
    except (OSError, subprocess.SubprocessError):
        return "Der Bildschirm lässt sich gerade nicht schalten."


# ---------- Ansagen aus den Daten der Seite ----------

def muell(_):
    html = seite()
    namen = dict(re.findall(r'"([^"]+)":\s*\{\s*name:\s*"([^"]+)"', html))   # muellArten der Konfiguration
    heute = jetzt().date().isoformat()
    naechste = {}
    for eintrag in du.lies_liste(html, "MUELL"):
        tag, art = eintrag.split(" ", 1)
        if tag >= heute:
            naechste.setdefault(art, tag)
    if not naechste:
        return "Ich kenne keine Mülltermine."
    nach_tag = {}
    for art, tag in sorted(naechste.items(), key=lambda x: x[1]):
        nach_tag.setdefault(tag, []).append(namen.get(art, art))
    return " ".join(f"{gross(tag_text(date.fromisoformat(tag)))}: {aufzaehlung(arten)}."
                    for tag, arten in list(nach_tag.items())[:3])


def bus(_):
    privat = du.lies_privat(ORDNER / "dashboard-privat.conf")
    steig, kontakt = privat.get("BUS_STEIG"), privat.get("KONTAKT")
    if not steig:
        return "Der Bus ist nicht eingerichtet."
    jetzt_utc = datetime.now(timezone.utc)
    abfahrten = []
    if kontakt:   # live mit Verspätungen, sonst der Fahrplan aus der Seite
        try:
            url = du.TRANSITOUS + "?" + urllib.parse.urlencode({"stopId": steig, "n": 10, "radius": 0})
            for f in json.loads(du.lade(url, f"{du.USER_AGENT} ({kontakt})"))["stopTimes"]:
                if f["place"].get("stopId") != steig or f.get("cancelled"):
                    continue
                ab, plan = zeitpunkt(f["place"]["departure"]), f["place"].get("scheduledDeparture")
                spaet = round((ab - zeitpunkt(plan)).total_seconds() / 60) if plan and f.get("realTime") else 0
                abfahrten.append((ab, f["routeShortName"], f["headsign"], spaet))
        except (OSError, ValueError, KeyError):
            abfahrten = []
    if not abfahrten:
        for eintrag in du.lies_liste(seite(), "BUS"):
            zeit, linie, ziel = eintrag.split(" ", 2)
            abfahrten.append((zeitpunkt(zeit), linie, ziel, 0))
    kommend = sorted(a for a in abfahrten if a[0] >= jetzt_utc - timedelta(seconds=30))[:3]
    if not kommend:
        return "Ich kenne gerade keine Abfahrten."
    saetze = []
    for ab, linie, ziel, spaet in kommend:
        minuten = int((ab - jetzt_utc).total_seconds() // 60)
        if minuten <= 0:
            wann_ab = "jetzt"
        elif minuten < 60:
            wann_ab = "in einer Minute" if minuten == 1 else f"in {minuten} Minuten"
        else:
            wann_ab = "um " + uhrzeit_text(ab.astimezone(BERLIN))
        saetze.append(f"{linie} Richtung {ziel} {wann_ab}" + (f", {spaet} Minuten verspätet" if spaet >= 2 else ""))
    return "Bus " + "; ".join(saetze) + "."


def termine(_):
    j = jetzt()
    heute, morgen = j.date().isoformat(), (j.date() + timedelta(days=1)).isoformat()
    tage = {heute: [], morgen: []}
    for eintrag in du.lies_liste(seite(), "KALENDER"):
        wann_text, titel = eintrag.split(" ", 1)
        tag = wann_text[:10]
        if tag not in tage:
            continue
        if "T" not in wann_text:
            tage[tag].append(titel)   # ganztägig
        elif wann_text >= j.strftime("%Y-%m-%dT%H:%M"):
            tage[tag].append(f"um {uhrzeit_text(datetime.fromisoformat(wann_text))} {titel}")
    return " ".join(f"{name}: {aufzaehlung(tage[tag])}." if tage[tag] else f"{name} steht nichts {mehr}an."
                    for tag, name, mehr in ((heute, "Heute", "mehr "), (morgen, "Morgen", "")))


def sprit(_):
    html = seite()
    treffer = re.search(r'sprit:\s*"(\w+)"', html)
    sorte = treffer.group(1) if treffer else "e10"
    name = {"e5": "Super", "e10": "E10", "diesel": "Diesel"}.get(sorte, sorte)
    stationen = du.lies_liste(html, "TANKEN")
    if not stationen:
        return "Ich kenne keine Spritpreise."
    if datetime.now(timezone.utc) - zeitpunkt(stationen[0]["zeit"]) > timedelta(hours=3):
        return "Die Spritpreise sind gerade nicht aktuell."
    offen = [s for s in stationen if s.get("offen") and s.get(sorte)]
    if not offen:
        return "Gerade hat keine Tankstelle in der Nähe offen."
    s = min(offen, key=lambda s: s[sorte])
    euro, rest = divmod(round(s[sorte] * 1000), 1000)
    preis = f"{euro} Euro {rest // 10}" + (f",{rest % 10}" if rest % 10 else "")
    return f"{name} gibt es am günstigsten bei {s['name']} für {preis}."


# ---------- Sport ----------

SPRECHNAMEN = {"S04": "Schalke"}
ALIAS = {"bvb": "dortmund", "sankt": "st", "niners": "49ers", "fck": "kaiserslautern", "lautern": "kaiserslautern",
         "ksc": "karlsruhe", "fcb": "bayern", "schalke": "schalke"}
GENERISCH = {"fc", "sv", "sc", "vfl", "vfb", "tsg", "fsv", "spvgg", "dsc", "bsc", "ssv", "tsv", "sg", "bv"}
LIGA_WOERTER = {"nfl", "football", "american", "liga", "bundesliga", "fussball", "erste", "ersten", "zweite", "zweiten"}
FUELLWOERTER = {"wie", "steht", "stehts", "s", "es", "bei", "beim", "im", "in", "der", "die", "das", "den", "dem", "des",
                "spielt", "spielen", "spiel", "spiele", "grad", "gerade", "aktuell", "jetzt", "was", "macht", "mit",
                "und", "gegen", "von", "vom", "ist", "sport", "ergebnis", "ergebnisse", "stand", "alles", "alle",
                "heute", "team", "mein", "meine", "the", "zeig", "zeige", "mir", "bitte", "wer", "hat", "gewonnen",
                "um", "ueberall", "spielstand", "spielstaende"}


def spiel(heim, gast, heim_woerter, gast_woerter, anpfiff, status, tore, info=""):
    return {"heim": heim, "gast": gast, "hw": set(heim_woerter), "gw": set(gast_woerter),
            "anpfiff": anpfiff, "status": status, "tore": tore, "info": info}


def openliga_spiel(m):
    anpfiff = zeitpunkt(m["matchDateTimeUTC"])
    ergebnisse = sorted(m.get("matchResults") or [], key=lambda r: r.get("resultOrderID") or 0)
    tor = max(m.get("goals") or [], key=lambda t: (t.get("scoreTeam1") or 0) + (t.get("scoreTeam2") or 0), default=None)
    if ergebnisse and (m.get("matchIsFinished") or not tor):
        tore = (ergebnisse[-1]["pointsTeam1"], ergebnisse[-1]["pointsTeam2"])
    elif tor:
        tore = (tor.get("scoreTeam1") or 0, tor.get("scoreTeam2") or 0)
    else:
        tore = (0, 0)
    seit = datetime.now(timezone.utc) - anpfiff
    if m.get("matchIsFinished") or seit > timedelta(minutes=150):
        status = "ende"
    else:
        status = "laeuft" if seit >= timedelta(0) else "vor"
    t1, t2 = m["team1"], m["team2"]
    name = lambda t: SPRECHNAMEN.get(t.get("shortName"), t.get("shortName") or t["teamName"])
    return spiel(name(t1), name(t2), woerter(f"{t1['teamName']} {t1.get('shortName') or ''}"),
                 woerter(f"{t2['teamName']} {t2.get('shortName') or ''}"), anpfiff, status, tore,
                 "live" if status == "laeuft" else "")


def openliga_runde(liga, saison=None, nummer=None):
    daten = json.loads(du.lade(OPENLIGADB + liga + (f"/{saison}/{nummer}" if nummer else "")))
    if not daten:
        return None
    gruppe, saison = daten[0]["group"], daten[0]["leagueSeason"]
    nummer = gruppe["groupOrderID"]
    return {"name": gruppe["groupName"],
            "spiele": sorted((openliga_spiel(m) for m in daten), key=lambda s: s["anpfiff"]),
            "vorige": (lambda: openliga_runde(liga, saison, nummer - 1)) if nummer > 1 else None,
            "frei": []}


def nfl_spiel(e):
    c = e["competitions"][0]
    st = c["status"]
    heim = next(t for t in c["competitors"] if t["homeAway"] == "home")
    gast = next(t for t in c["competitors"] if t["homeAway"] == "away")
    status = {"in": "laeuft", "post": "ende"}.get(st["type"]["state"], "vor")
    info = ""
    if st["type"].get("name") in ("STATUS_POSTPONED", "STATUS_CANCELED"):
        status, info = "vor", "abgesagt"
    elif status == "laeuft":
        periode = st.get("period") or 1
        info = ("Halbzeit" if st["type"].get("name") == "STATUS_HALFTIME"
                else "Verlängerung" if periode > 4 else f"{periode}. Viertel")
    team = lambda t: woerter(f"{t['team']['displayName']} {t['team'].get('abbreviation', '')}")
    return spiel(heim["team"]["shortDisplayName"], gast["team"]["shortDisplayName"], team(heim), team(gast),
                 zeitpunkt(e["date"]), status, (int(heim.get("score") or 0), int(gast.get("score") or 0)), info)


def nfl_runde(woche=None, typ=None):
    daten = json.loads(du.lade(ESPN_NFL + (f"?week={woche}&seasontype={typ}" if woche else "")))
    woche, typ = daten["week"]["number"], daten["season"]["type"]
    vorige = (woche - 1, typ) if woche > 1 else (18, 2) if typ == 3 else None
    return {"name": {2: f"Woche {woche}", 3: f"Playoffs, Runde {woche}"}.get(typ, f"Vorbereitung, Woche {woche}"),
            "spiele": sorted((nfl_spiel(e) for e in daten.get("events", [])), key=lambda s: s["anpfiff"]),
            "vorige": (lambda: nfl_runde(*vorige)) if vorige else None,
            "frei": [(t["shortDisplayName"], set(woerter(f"{t['displayName']} {t.get('abbreviation', '')}")))
                     for t in daten["week"].get("teamsOnBye", [])]}


LIGEN = {"bl1": ("Bundesliga", lambda: openliga_runde("bl1")),
         "bl2": ("2. Bundesliga", lambda: openliga_runde("bl2")),
         "nfl": ("NFL", nfl_runde)}


def ergebnis_text(s):
    if s["status"] == "vor":
        return f"{s['heim']} gegen {s['gast']} {wann(s['anpfiff'])}"
    h, g = s["tore"]
    info = s["info"] if s["info"] != "live" else ""
    return f"{s['heim']} gegen {s['gast']} {h} zu {g}" + (f", {info}" if info else "")


def anzeige_spiel(s, markiert=()):
    h, g = s["tore"]
    return {"heim": s["heim"], "gast": s["gast"], "status": s["status"],
            "stand": "" if s["status"] == "vor" else f"{h}:{g}",
            "info": kurz_wann(s["anpfiff"]) if s["status"] == "vor" else s["info"],
            "markiert": "heim" if s["hw"] in markiert else "gast" if s["gw"] in markiert else ""}


def anzeige_runde(liga, runde, spiele=None, markiert=(), hinweis=""):
    return {"name": liga, "runde": runde["name"], "hinweis": hinweis,
            "spiele": [anzeige_spiel(s, markiert) for s in (runde["spiele"] if spiele is None else spiele)]}


def weiter_text(spiele):
    offen = [s for s in spiele if s["status"] == "vor" and s["info"] != "abgesagt"]
    return f"weiter geht es {wann(offen[0]['anpfiff'])} mit {offen[0]['heim']} gegen {offen[0]['gast']}" if offen else ""


def weiter_hinweis(spiele):
    offen = [s for s in spiele if s["status"] == "vor" and s["info"] != "abgesagt"]
    return f"Weiter: {kurz_wann(offen[0]['anpfiff'])}, {offen[0]['heim']} – {offen[0]['gast']}" if offen else ""


def bericht_liga(kurz):
    """Ganze Runde: die laufende, sonst die Ergebnisse der letzten."""
    liga, lade_runde = LIGEN[kurz]
    runde = lade_runde()
    if not runde or not runde["spiele"]:
        return f"{liga}: keine Spiele gefunden.", None
    spiele = runde["spiele"]
    laufend = [s for s in spiele if s["status"] == "laeuft"]
    beendet = [s for s in spiele if s["status"] == "ende"]
    offen = [s for s in spiele if s["status"] == "vor"]
    if laufend or beendet:
        teile = [f"{liga}, {runde['name']}."]
        if laufend:
            teile.append("Live: " + "; ".join(map(ergebnis_text, laufend)) + ".")
        if beendet:
            teile.append("Beendet: " + "; ".join(map(ergebnis_text, beendet)) + ".")
        if offen:
            teile.append(f"Noch {len(offen)} {'Spiel' if len(offen) == 1 else 'Spiele'}, {weiter_text(offen)}.")
        return " ".join(teile), anzeige_runde(liga, runde)
    vorige = runde["vorige"]() if runde["vorige"] else None
    weiter = weiter_text(spiele)
    if not vorige or not vorige["spiele"]:
        return f"{liga}: {gross(weiter)}." if weiter else f"{liga}: keine Spiele.", anzeige_runde(liga, runde)
    text = f"{liga}, {vorige['name']}: " + "; ".join(map(ergebnis_text, vorige["spiele"])) + "."
    return text + (f" {gross(weiter)}." if weiter else ""), anzeige_runde(liga, vorige, hinweis=weiter_hinweis(spiele))


def bericht_kurz(kurz):
    """Für "alles": was gerade läuft oder in den letzten 12 Stunden zu Ende ging, sonst das nächste Spiel."""
    liga, lade_runde = LIGEN[kurz]
    runde = lade_runde()
    if not runde or not runde["spiele"]:
        return f"{liga}: keine Spiele gefunden.", None
    spiele = runde["spiele"]
    laufend = [s for s in spiele if s["status"] == "laeuft"]
    eben = [s for s in spiele if s["status"] == "ende"
            and datetime.now(timezone.utc) - s["anpfiff"] < timedelta(hours=12)]
    weiter = weiter_text(spiele)
    teile = []
    if laufend:
        teile.append("live " + "; ".join(map(ergebnis_text, laufend)))
    if eben:
        teile.append("zuletzt " + "; ".join(map(ergebnis_text, eben)))
    if not teile:
        teile.append("gerade kein Spiel" + (f", {weiter}" if weiter else ""))
    naechste = [s for s in spiele if s["status"] == "vor"][:3]
    return (f"{liga}: " + ". ".join(teile) + ".",
            anzeige_runde(liga, runde, spiele=laufend + eben + naechste))


def punkte(such, team):
    return len({w for w in such if not w.isdigit() and w not in GENERISCH} & team)


def bericht_team(such, ligen):
    treffer = []   # (Punkte, Liga, Runde, Spiel oder None bei spielfrei, Team-Wörter, Name)
    for kurz in ligen:
        try:
            runde = LIGEN[kurz][1]()
        except (OSError, ValueError, KeyError):
            continue
        if not runde:
            continue
        for s in runde["spiele"]:
            for seite_ in ("h", "g"):
                p = punkte(such, s[seite_ + "w"])
                if p:
                    treffer.append((p, kurz, runde, s, s[seite_ + "w"], s["heim" if seite_ == "h" else "gast"]))
        for name, team in runde["frei"]:
            p = punkte(such, team)
            if p:
                treffer.append((p, kurz, runde, None, team, name))
    if not treffer:
        return "Dazu habe ich kein Team gefunden.", [], ""
    beste = max(t[0] for t in treffer)
    treffer = [t for t in treffer if t[0] == beste][:3]
    saetze, abschnitte = [], []
    for _, kurz, runde, s, team, name in treffer:
        liga = LIGEN[kurz][0]
        zuletzt = None
        if s is None or s["status"] == "vor":
            vorige = runde["vorige"]() if runde["vorige"] else None
            zuletzt = next((v for v in (vorige or {}).get("spiele", []) if team in (v["hw"], v["gw"])), None)
            if zuletzt:
                abschnitte.append(anzeige_runde(liga, vorige, spiele=[zuletzt], markiert=[team]))
        if s is None:
            satz = f"{name}: diese Woche spielfrei."
        elif s["status"] == "laeuft":
            satz = f"Live: {ergebnis_text(s)}."
        elif s["status"] == "ende":
            satz = f"Endstand: {ergebnis_text(s)}."
        elif s["hw"] == team:
            satz = f"{name} spielt {wann(s['anpfiff'])} zu Hause gegen {s['gast']}."
        else:
            satz = f"{name} spielt {wann(s['anpfiff'])} bei {s['heim']}."
        if zuletzt:
            satz += f" Zuletzt: {ergebnis_text(zuletzt)}."
        saetze.append(satz)
        if s:
            abschnitte.append(anzeige_runde(liga, runde, spiele=[s], markiert=[team]))
    return " ".join(saetze), abschnitte, aufzaehlung(dict.fromkeys(t[5] for t in treffer))


def zeige_auf_dashboard(titel, abschnitte):
    abschnitte = [a for a in abschnitte if a and a["spiele"]]
    if not abschnitte:
        return
    daten = {"zeit": int(time.time() * 1000), "titel": titel, "ligen": abschnitte}
    inhalt = json.dumps(daten, ensure_ascii=False).replace("<", "\\u003c")
    du.schreibe_datei(ORDNER / "dashboard-sport.js", f"sportAnzeige({inhalt});\n")
    wecke_bildschirm()


def sport(rest):
    such = [ALIAS.get(w, w) for w in rest]
    w = set(such)
    ligen = set()
    if w & {"nfl", "football"}:
        ligen.add("nfl")
    if w & {"liga", "bundesliga", "fussball"}:
        zweite, erste = bool(w & {"zweite", "zweiten", "2"}), bool(w & {"erste", "ersten", "1"})
        if "fussball" in w and not (zweite or erste):
            ligen |= {"bl1", "bl2"}
        else:
            if erste or not zweite:
                ligen.add("bl1")
            if zweite:
                ligen.add("bl2")
    ligen = [k for k in LIGEN if k in ligen]
    team = [x for x in such if x not in FUELLWOERTER and x not in LIGA_WOERTER
            and not x.isdigit() and x not in GENERISCH]
    if team:
        text, abschnitte, titel = bericht_team(team, ligen or list(LIGEN))
        zeige_auf_dashboard(titel or "Sport", abschnitte)
        return text
    saetze, abschnitte = [], []
    for kurz in ligen or LIGEN:
        try:
            satz, abschnitt = (bericht_liga if ligen else bericht_kurz)(kurz)
        except (OSError, ValueError, KeyError):
            satz, abschnitt = f"{LIGEN[kurz][0]}: gerade nicht erreichbar.", None
        saetze.append(satz)
        abschnitte.append(abschnitt)
    zeige_auf_dashboard(LIGEN[ligen[0]][0] if len(ligen) == 1 else "Sport", abschnitte)
    return " ".join(saetze)


# ---------- Aufruf ----------

BEFEHLE = {"aktualisieren": aktualisieren, "update": aktualisieren, "neustart": neustart, "neustarten": neustart,
           "bildschirm": bildschirm, "muell": muell, "bus": bus, "termine": termine, "kalender": termine,
           "sprit": sprit, "tanken": sprit, "sport": sport}


def stdin_text():
    """Text aus dem Eingabefeld des Kurzbefehls, z. B. ein diktierter Teamname."""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return ""
        bereit, _, _ = select.select([sys.stdin], [], [], 0.3)
        return os.read(sys.stdin.fileno(), 1000).decode("utf-8", "replace") if bereit else ""
    except (OSError, ValueError):
        return ""


def eingabe():
    teile = sys.argv[1:]
    # Bei einem SSH-Schlüssel mit command="..." in authorized_keys steht der Befehl hier
    if not teile and os.environ.get("SSH_ORIGINAL_COMMAND"):
        try:
            teile = shlex.split(os.environ["SSH_ORIGINAL_COMMAND"])
        except ValueError:
            teile = os.environ["SSH_ORIGINAL_COMMAND"].split()
        for i, t in enumerate(teile):
            if t.endswith("dashboard-befehl.py"):
                teile = teile[i + 1:]
                break
    return " ".join(teile + [stdin_text()])


def main():
    worte = woerter(eingabe())
    if not worte or worte[0] not in BEFEHLE:
        print("Befehle: aktualisieren, neustart, bildschirm an, bildschirm aus, muell, bus, termine, sprit, sport.")
        return
    try:
        print(BEFEHLE[worte[0]](worte[1:]))
    except urllib.error.URLError:
        print("Ich erreiche den Server gerade nicht.")
    except Exception as fehler:   # Siri soll immer etwas vorlesen können
        print(f"Das hat nicht geklappt: {fehler}")


if __name__ == "__main__":
    main()
