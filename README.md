<p align="center">
  <img src="docs/banner.png" alt="Spiel Saison" width="520">
</p>

# Spiel Saison für Home Assistant

[![Tests](https://github.com/RedDragonNode/ha-spiel-saison/actions/workflows/tests.yaml/badge.svg)](https://github.com/RedDragonNode/ha-spiel-saison/actions/workflows/tests.yaml)
[![Hassfest](https://github.com/RedDragonNode/ha-spiel-saison/actions/workflows/hassfest.yaml/badge.svg)](https://github.com/RedDragonNode/ha-spiel-saison/actions/workflows/hassfest.yaml)
[![HACS](https://github.com/RedDragonNode/ha-spiel-saison/actions/workflows/hacs.yaml/badge.svg)](https://github.com/RedDragonNode/ha-spiel-saison/actions/workflows/hacs.yaml)

**Saisons, Spielpartner und Aufnahmen aus einer Hand.** Jeder Spielpartner hat feste Kameras, eigene Saisons und eigene Zähler. Ein Schalter richtet den Ort ein (Kameras anfahren, Licht, Winde, Ansage), wartet den Vorlauf ab und startet die Aufnahme. Beim Beenden stellt er alles wieder zurück. Aufgenommen wird der RTSP-Stream direkt in eine Datei, **ohne Neukodierung** (ffmpeg `-c copy`), das braucht kaum CPU und keine GPU.

Die Integration ist der Nachfolger von „Spiel Saison aufnehmen“ und gedacht für die Kameras der Integration [Ctronics 4K Cam](https://github.com/RedDragonNode/ha-ctronics-cam). Die Aufnahme funktioniert mit jeder Kamera-Entity, die eine Stream-Quelle hat. Nur die PTZ-Voreinstellung fährt nur Ctronics-Kameras an.

| Baustein | Aufgabe |
|---|---|
| Integration `spiel_saison` | Spielpartner, Orte, Saisons, Zähler, Auswertung, Ablauf und Sensoren |
| App `spiel_saison_recorder` | schreibt die Streams auf die Platte, überwacht sie und startet bei Abbruch neu, läuft auch bei einem HA-Neustart weiter |

```
Integration (Ablauf, Zähler)  ──HTTP──▶  App „Spiel Saison Recorder“  ──ffmpeg -c copy──▶  Kamera (RTSP)
                                          pro Kamera ein ffmpeg, Neustart bei Stillstand          │
                                                                                                  ▼
                       /media/Aufnahmen/<Basis>/Spielen mit Frank #02/Saison 01 vom 15,10,2026/#02.01.03.Cam ID.Fuchsrevier 15.10.2026.mp4
```

## Installation

1. **HACS:** Unter *HACS → ⋮ → Benutzerdefinierte Repositories* `https://github.com/RedDragonNode/ha-spiel-saison` als **Integration** hinzufügen, **Spiel Saison** installieren und Home Assistant neu starten.
2. **App:** Im App-Store oben rechts *⋮ → Repositories* wählen und dieselbe Adresse hinzufügen. **Spiel Saison Recorder** installieren, unter *Konfiguration* einen `api_key` (mindestens 12 Zeichen) setzen, den `aufnahme_ordner` einstellen (z. B. `/media/Aufnahmen`) und starten. Einstellungen und Dateien beschreibt [die Dokumentation der App](spiel_saison_recorder/DOCS.md).
3. **Integration:** *Einstellungen → Geräte & Dienste → Integration hinzufügen → Spiel Saison.* Adresse der App (steht auf deren Seite unter *Info → Hostname*, Port 8099) und derselbe `api_key`.

## Einrichten

Die Verbindung hat dazu die **Ordner-Muster**, die später unter *Verbindung und Ordner ändern* änderbar sind. Neue Muster gelten ab der nächsten Aufnahme.

| Feld | Standard |
|---|---|
| Basis-Ordner | `Spiel_Saison` (unter dem Aufnahme-Ordner der App, auch mit Unterordner wie `Spielen/Spezial`) |
| Ordner je Spielpartner | `Spielen mit {partner} #{nr}` |
| Ordner je Saison | `Saison {saison} vom %DD,%MM,%CCYY` (das Datum ist der **Beginn der Saison**) |
| Dateiname je Kamera | `#{nr}.{saison}.{aufnahme}.Cam ID.{cam} %DD.%MM.%CCYY` |

Platzhalter in geschweiften Klammern: `{partner}` Name, `{nr}` Nummer des Spielpartners, `{saison}` Saison, `{aufnahme}` Nummer der Aufnahme in der Saison, `{cam}` Name der Kamera (der Gerätename), `{cam_nr}` Nummer der Kamera beim Spielpartner (die beiden letzten nur im Dateinamen), `{ort}` Ort. Zahlen sind zweistellig (`03`). Dazu Datum und Zeit mit `%CCYY %MM %DD %hh %mm %ss`. Heißen zwei Dateien gleich, hängt die Integration `Cam NN` an.

### Spielpartner

*Spiel Saison → Spielpartner hinzufügen:* Name, Nummer (für die Muster, jede Nummer nur einmal) und die **festen Kameras**. Dieselbe Kamera darf bei mehreren Spielpartnern stehen, sie nimmt aber nie bei zwei zugleich auf. Nach einem Neustart von Home Assistant erkennt die Integration laufende Aufnahmen wieder. Wird eine Kamera-Entity umbenannt, ziehen die Spielpartner mit.

### Orte

*Spiel Saison → Ort hinzufügen* (Bett, Schaukel …), in drei Schritten, alles außer dem Namen ist optional:

| Schritt | Einstellung |
|---|---|
| 1 | PTZ-Voreinstellung der Ctronics-Kameras (0 = nicht bewegen), **Vorlauf** (Standard 30 s), **Pause** vor dem Ende-Schritt (Standard 10 s), Ansage über eine `notify`-Entity mit Texten für Start und Ende |
| 2 | **Vorbereiten:** einschalten, dazu Rollo oder Winde auf eine Position fahren (davor beginnt die Saison des Tages, siehe unten). **Beginn:** erst ausschalten, dann einschalten |
| 3 | **Ende:** erst ausschalten, dann einschalten |

Die „Ziele“ wählst du wie in Automationen: Entities, Geräte, Bereiche oder **Labels**, auch Szenen und Skripte. Aufgerufen wird `homeassistant.turn_on` und `turn_off`.

**Ablauf beim Starten:** Kameras auf die Voreinstellung fahren → Beginn „ausschalten“ → Beginn „einschalten“ → Ansage → Vorlauf → Aufnahme. **Beim Beenden:** Aufnahme stoppen → Pause → Ende „ausschalten“ → Ende „einschalten“ → Ansage. Schlägt ein Aufruf fehl, läuft der Rest trotzdem weiter, die Aufnahme ist wichtiger als das Licht (der Fehler steht im Protokoll und im Attribut `message` des Status-Sensors). Wird beim Vorlauf gestoppt, fällt die Aufnahme aus und der Ende-Schritt stellt alles zurück. Lehnt die App den Start ab (z. B. Speicher voll), läuft der Ende-Schritt ebenfalls.

### Saison

Eine Saison ist ein Spieltag mit allen Aufnahmen darin, sie bekommt einen eigenen Ordner. **Pro Tag beginnt höchstens eine Saison:** Das **Vorbereiten** (Taste oder Dienst) beginnt die neue Saison, wenn es für heute noch keine gibt, und bereitet danach den Ort vor. Drückst du am selben Tag noch einmal auf Vorbereiten, bleibt die Saison, wie sie ist, und nur der Ort wird vorbereitet. Läuft bei dem Spielpartner gerade eine Aufnahme, ändert Vorbereiten die Saison nie. Die Taste **Neue Saison beginnen** und der Dienst `new_season` gelten dieselbe Regel (die Taste meldet es, wenn es für heute schon eine Saison gibt, der Dienst antwortet mit `started: false`). Ein neu angelegter Spielpartner startet mit Saison 1 am Tag des Anlegens, die nächste beginnt also frühestens am Folgetag.

## Entities

Am Gerät **Spiel Saison**: `select` **Ort** (der Ort für Vorbereiten und Aufnahme, nur mit mindestens einem Ort), Sensor **Status** (Bereit, Nimmt auf, Fehler), **Aktueller Spielpartner**, **Recorder: freier Speicher**, Binärsensoren **Recorder erreichbar** und **Aufnahme**.

Am Gerät **Spielpartner …**:

| Entity | Bedeutung |
|---|---|
| Schalter **Aufnahme** | an = Ablauf starten, aus = Aufnahme beenden |
| Taste **Vorbereiten** | beginnt die Saison des Tages (nur wenn es für heute noch keine gibt) und führt den Vorbereiten-Schritt des gewählten Ortes aus |
| Taste **Neue Saison beginnen** | Saison +1, Zähler und Auswertung der Saison auf null, neuer Saison-Ordner. Geht nur einmal pro Tag und nur ohne laufende Aufnahme, die alte Saison wird gespeichert |
| Zahlen **Saison**, **Aufnahmen in Saison**, **Aufnahmen gesamt** | Zähler, von Hand änderbar |
| Sensor **Status** | Bereit, Startet, Nimmt auf, Beendet |
| Sensoren **Aufnahmedauer in Saison / gesamt** (Stunden), **Längste Aufnahme in Saison** (Minuten), **Letzte Aufnahme** | Auswertung |
| Sensor **Zielordner** | Ordner der laufenden oder nächsten Aufnahme, der volle Pfad steht im Attribut `path` |

Die Zähler zählen eine Aufnahme, sobald die App sie gestartet hat.

## Dienste und Ereignisse

| Dienst | Felder |
|---|---|
| `spiel_saison.start_recording` | `partner`, optional `place` (sonst der gewählte Ort) |
| `spiel_saison.stop_recording` | `partner` |
| `spiel_saison.prepare` | `partner`, optional `place`; beginnt auch die Saison des Tages (Antwort: `season`, `new_season`) |
| `spiel_saison.new_season` | `partner`; einmal pro Tag (Antwort: `started`, bei `false` mit `reason: already_today`) |

`partner` und `place` sind die Namen, Groß- und Kleinschreibung und Sonderzeichen sind egal. Die Dienste liefern eine Antwort mit dem Ergebnis.

Ereignisse für eigene Automationen (zum Beispiel Benachrichtigungen):

| Ereignis | Daten |
|---|---|
| `spiel_saison_recording_started` | `partner`, `partner_id`, `season`, `recording`, `place`, `folder`, `cams` |
| `spiel_saison_recording_stopped` | `partner`, `partner_id`, `season`, `recording`, `place`, `reason` (`manual`, `cancelled`, vom Recorder gemeldeter Grund), `duration_s` |
| `spiel_saison_season_started` | `partner`, `partner_id`, `season`, `start` |

Beendet der Recorder eine Aufnahme von sich aus (zum Beispiel weil der Speicher voll ist), räumt die Integration auf, sendet das Stopp-Ereignis und legt unter *Einstellungen → System → Reparaturen* einen Hinweis an.

## Von „Spiel Saison aufnehmen“ umsteigen

Zähler werden nicht übernommen, sie beginnen bei null. Setze sie bei Bedarf über die Zahlen am Gerät des Spielpartners. Automationen, die die alten Tasten und Entities benutzen, stellst du auf die Taste **Vorbereiten** (sie beginnt auch die Saison des Tages), den Schalter **Aufnahme** des Spielpartners und den **Ort** um, danach kannst du die alte Integration und die alte App entfernen.

## Fehlersuche

- **Recorder nicht erreichbar:** Hostname und Port der App prüfen, den `api_key` mit dem in der Integration vergleichen. Stimmt er nicht, startet Home Assistant die Neuanmeldung.
- **Keine Kamera frei oder ohne Stream-Quelle:** Der Fehler nennt je Kamera den Grund (`missing`, `busy`, `no_stream`). Nimm am besten den Hauptstream in voller Auflösung.
- **Aufnahme bricht ab:** In der App steht pro Kamera der letzte ffmpeg-Fehler, mit `ffmpeg_loglevel: info` mehr. Die Diagnose der Integration (*… → Diagnose herunterladen*) enthält keine Schlüssel und keine Stream-Adressen.

## Entwicklung

```bash
pip install -r requirements_test.txt
ruff check . && ruff format --check .
pytest
```

Die Tests der App brauchen `ffmpeg` und laufen sonst nur teilweise. In der CI laufen Hassfest, die HACS-Prüfung und alle Tests auf jedem Push.

## English

**Spiel Saison** manages seasons, play partners and recordings in Home Assistant. Every play partner has fixed cameras, their own seasons and counters. One switch sets up a place (move the Ctronics cameras to a PTZ preset, switch lights and devices, make an announcement), waits for the lead time and starts a stream-copy recording through the app **Spiel Saison Recorder** (ffmpeg `-c copy`, no re-encoding). When you stop, it waits, puts everything back and updates the statistics. Preparing a place also begins the season of the day, at most one season begins per day. Folder and file names come from patterns with placeholders for partner, season, recording number and camera name. The integration offers services and events for your own automations. Install the integration through HACS and add this repository to the App store for the recorder. The texts in the UI are available in German and English.

## Lizenz

[MIT](LICENSE)
