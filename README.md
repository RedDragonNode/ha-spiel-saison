<p align="center">
  <img src="docs/banner.png" alt="Spiel Saison" width="520">
</p>

# Spiel Saison für Home Assistant

[![Tests](https://github.com/RedDragonNode/ha-spiel-saison/actions/workflows/tests.yaml/badge.svg)](https://github.com/RedDragonNode/ha-spiel-saison/actions/workflows/tests.yaml)
[![Hassfest](https://github.com/RedDragonNode/ha-spiel-saison/actions/workflows/hassfest.yaml/badge.svg)](https://github.com/RedDragonNode/ha-spiel-saison/actions/workflows/hassfest.yaml)
[![HACS](https://github.com/RedDragonNode/ha-spiel-saison/actions/workflows/hacs.yaml/badge.svg)](https://github.com/RedDragonNode/ha-spiel-saison/actions/workflows/hacs.yaml)

**Saisons, Spielpartner und Aufnahmen aus einer Hand.** Jeder Spielpartner hat eigene Orte mit ihren Kameras, eigene Saisons und eigene Zähler. Ein Schalter richtet den Ort ein (Kameras auf ihre PTZ-Position fahren, Licht, Winde, Ansage), wartet den Vorlauf ab und startet die Aufnahme. Beim Beenden stellt er alles wieder zurück. Aufgenommen wird der RTSP-Stream direkt in eine Datei, **ohne Neukodierung** (ffmpeg `-c copy`), das braucht kaum CPU und keine GPU.

Die Integration ist der Nachfolger von „Spiel Saison aufnehmen“ und gedacht für die Kameras der Integration [Ctronics 4K Cam](https://github.com/RedDragonNode/ha-ctronics-cam). Die Aufnahme funktioniert mit jeder Kamera-Entity, die eine Stream-Quelle hat. Nur die PTZ-Positionen fahren ausschließlich Ctronics-Kameras an.

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

Platzhalter in geschweiften Klammern: `{partner}` Name, `{nr}` Nummer des Spielpartners, `{saison}` Saison, `{aufnahme}` Nummer der Aufnahme in der Saison, `{cam}` Präfix der Kamera (bei Ctronics 4K Cam das Präfix, das man beim Hinzufügen der Kamera einträgt, hat eine Kamera keins, steht dort ihr Name, der Gerätename), `{cam_nr}` Nummer der Kamera beim Spielpartner (die beiden letzten nur im Dateinamen), `{ort}` Ort. Zahlen sind zweistellig (`03`). Dazu Datum und Zeit mit `%CCYY %MM %DD %hh %mm %ss`. Heißen zwei Dateien gleich, hängt die Integration `Cam NN` an.

### Spielpartner

*Spiel Saison → Spielpartner hinzufügen:* Name, Nummer (für die Muster, jede Nummer nur einmal) und optional:

| Feld | Bedeutung |
|---|---|
| **Spitzname** | Platzhalter `{nickname}` in den Ansagen (ohne Spitzname steht dort der Name) |
| **Eigene Vorlieben**, **Vorlieben zum Bearbeiten seines Partners** | zwei Felder, in die du eigene Einträge tippst (jeden mit Enter bestätigen). Alles, was du je eingegeben hast, steht bei jedem weiteren Spielpartner zur Wahl |
| **Ansage** | eine `notify`-Entity (zum Beispiel ein Echo) mit je einem Text für den Start und das Ende einer Aufnahme. In den Texten gehen `{nickname}`, `{partner}`, `{nr}`, `{saison}` und `{ort}` |

Beim **Bearbeiten** eines Spielpartners (beim Anlegen noch nicht) kommen unten die Zähler und das Zurücksetzen der Auswertung dazu:

| Feld | Bedeutung |
|---|---|
| **Saison**, **Aufnahmen in Saison**, **Aufnahmen gesamt** | die Zähler, wie sie gerade stehen. Übernommen wird nur, was du änderst. Eine Aufnahme, die währenddessen zu Ende geht, bleibt gezählt |
| **Aufnahmedauer in Saison auf null setzen**, **Aufnahmedauer gesamt auf null setzen**, **Längste Aufnahme in Saison auf null setzen** | Schalter, die beim Speichern die jeweilige Auswertung auf null setzen. Danach stehen sie wieder aus |

Am Gerät des Spielpartners sind die Zähler Sensoren. Ändern lassen sie sich nur hier.

Die Kameras gehören nicht zum Spielpartner, sondern zu seinen Orten. Nach einem Neustart von Home Assistant erkennt die Integration laufende Aufnahmen wieder.

### Vorlieben

Die Vorlieben gibst du direkt beim Spielpartner ein, es braucht keine eigenen Einträge dafür. Neue Namen merkt sich die Integration im Hintergrund, bei jedem weiteren Spielpartner stehen sie zur Wahl. Am **Gerät des Spielpartners** bekommt jede Vorliebe einen Sensor unter **Diagnose**. Die **Vorlieben zum Bearbeiten** haben außerdem einen Schalter und einen Zähler, bei den eigenen Vorlieben gibt es nur den Diagnose-Sensor:

| Entity | Bedeutung |
|---|---|
| Sensor (unter **Diagnose**) | die Vorliebe für sich, mit dem Namen der Vorliebe. Der Zustand nennt die Art (eigene Vorliebe oder Vorliebe zum Bearbeiten), bei den Vorlieben zum Bearbeiten zeigt das Attribut `active`, ob sie gerade „dabei“ ist |
| Schalter **… dabei** (nur Vorlieben zum Bearbeiten) | schaltest du vor dem Spielen ein, was dabei sein soll. Der Schalter bleibt, wie er ist, bis du ihn änderst |
| Sensor **… Zähler** (nur Vorlieben zum Bearbeiten) | bei wie vielen Aufnahmen die Vorliebe „dabei“ war. Beim Start einer Aufnahme zählt jede eingeschaltete Vorliebe +1 |

Fügst du beim Bearbeiten des Spielpartners eine Vorliebe hinzu, tauchen ihre Entities nach dem Speichern am Gerät auf, gestrichene verschwinden dort samt Entities. Der Zähler läuft unter dem Namen der Vorliebe weiter, solange er gleich bleibt. Die Listen stehen auch in den Attributen `nickname`, `likes` und `treats` des Status-Sensors, zum Beispiel für eigene Automationen.

### Beleuchtung des Spielpartners

Jeder Spielpartner kann sich seine **Beleuchtung** einstellen und speichern lassen:

1. Beim Spielpartner (*Bearbeiten*) wählst du unter **Lichter** die Lichter, die der Spielpartner einstellen darf. Zu jedem dieser Lichter legt die Integration am **Gerät des Spielpartners** einen Regler an. Er heißt wie die Lampe, zeigt ihren Zustand (an oder aus, Helligkeit, Farbe oder Farbtemperatur) und gibt jede Einstellung an die echte Lampe weiter.
2. Der Spielpartner stellt seine Lichter am Gerät so ein, wie es ihm am liebsten ist: an oder aus, Farbe, Helligkeit. Die echte Lampe lässt sich auch weiter in Home Assistant oder in der Lampen-App einstellen, der Regler folgt ihr.
3. Am selben Gerät drückt er die Taste **Beleuchtung speichern** (oder du rufst den Dienst `spiel_saison.save_lighting` auf). Gespeichert wird bei jedem seiner Lichter, ob es an ist, die Farbe und die Helligkeit.

Die Beleuchtung stellt sich **mit der ersten Aufnahme jeder Saison** wieder ein, nach „Aufnahme beginnen“ und bevor der Vorlauf läuft. Bei den weiteren Aufnahmen der Saison bleiben die Lichter, wie sie sind. Gespeichert ist sie, bis du die Taste wieder drückst. Lichter, die nicht mehr in der Liste stehen, verlieren ihren Regler und fallen aus der gespeicherten Beleuchtung heraus. Bei Lampen, die ein Weiß melden (fast ohne Farbe), geht das Weiß als Farbtemperatur an die Lampe, wie bei „Saison vorbereiten“.

Schickt man Helligkeit und Farbe gleichzeitig an den Regler, geht erst die Helligkeit an die Lampe und nach kurzer Pause die Farbe, wie bei „Saison vorbereiten“. Die Regler der Integration selbst stehen in der Auswahl der Lichter nicht zur Wahl.

### Orte

*Spiel Saison → Ort hinzufügen* (Bett, Schaukel …). **Jeder Ort gehört zu einem Spielpartner** und bringt die Kameras mit, die dort aufnehmen. Ein Name darf bei mehreren Spielpartnern vorkommen („Bett“ bei Frank und „Bett“ bei Tobias sind zwei Orte). Der Dialog hat fünf Schritte (vier, wenn der Ort keine Ctronics-Kamera hat), alles außer Name, Spielpartner und Kameras ist optional:

| Schritt | Einstellung |
|---|---|
| 1 | **Name**, darunter der **Spielpartner** (Auswahlfeld), die **Kameras** des Ortes, **Vorlauf** (Standard 30 s) vor „Aufnahme beginnen“ und **Pause** vor „Aufnahme beenden“ (Standard 10 s) |
| 2 | **PTZ-Position je Kamera:** für jede ausgewählte Ctronics-Kamera ein eigenes Auswahlfeld mit ihren gespeicherten Positionen (oder „Nicht bewegen“). Der Schritt entfällt, wenn der Ort keine Ctronics-Kamera hat |
| 3 | **Position der Winde** (optional): eine Zahl von 0 bis 100 %. Diese Position fährt die Winde an, wenn du bei diesem Ort die **Aufnahme** startest, noch vor „Aufnahme beginnen“. „Saison vorbereiten“ fährt die Winde nur auf die Position von *Konfigurieren* (siehe unten). Die Winde selbst wählst du nur unter *Konfigurieren*. Leer lassen heißt: die Winde bleibt, wo sie ist |
| 4 | **Aufnahme beginnen:** erst ausschalten, dann einschalten |
| 5 | **Aufnahme beenden:** erst ausschalten, dann einschalten |

Die Namen der Positionen stammen aus der Integration Ctronics 4K Cam (dort unter *Optionen → Preset-Positionen*, eine Zeile `1: Schaukel` je Position). Hat eine Kamera noch keine Namen, stehen die Nummern 1 bis 64 zur Wahl. Dieselbe Kamera darf bei mehreren Orten stehen, sie nimmt aber nie bei zwei Spielpartnern zugleich auf. Wird eine Kamera-Entity umbenannt, ziehen die Orte mit.

Die „Ziele“ wählst du wie in Automationen: Entities, Geräte, Bereiche oder **Labels**, auch Szenen und Skripte. Aufgerufen wird `homeassistant.turn_on` und `turn_off`.

**Ablauf beim Starten:** Winde auf die Position des Ortes fahren (falls er eine hat) → Kameras auf ihre PTZ-Position fahren → „Aufnahme beginnen“ ausschalten → „Aufnahme beginnen“ einschalten → bei der ersten Aufnahme der Saison die gespeicherte Beleuchtung des Spielpartners einstellen → Ansage des Spielpartners → Vorlauf → Aufnahme. **Beim Beenden:** Aufnahme stoppen → Pause → „Aufnahme beenden“ ausschalten → „Aufnahme beenden“ einschalten → Ansage des Spielpartners. Schlägt ein Aufruf fehl, läuft der Rest trotzdem weiter, die Aufnahme ist wichtiger als das Licht (der Fehler steht im Protokoll und im Attribut `message` des Status-Sensors). Wird beim Vorlauf gestoppt, fällt die Aufnahme aus und „Aufnahme beenden“ stellt alles zurück. Lehnt die App den Start ab (z. B. Speicher voll), läuft „Aufnahme beenden“ ebenfalls. Ein Spielpartner ohne Ort kann nicht aufnehmen.

### Saison vorbereiten und Saison beenden

Die Tasten **Saison vorbereiten** und **Saison beenden** am Gerät jedes Spielpartners (und die gleichnamigen Dienste) haben eigene Einstellungen, die **von Anfang an da sind** und sich nicht löschen lassen: auf der Seite der Integration *Spiel Saison → Konfigurieren*. Sie gelten für alle Spielpartner und Orte gleich und nur für die beiden Tasten, nicht für die Aufnahme. Jede Taste hat eine Seite mit denselben Feldern:

| Feld | Bedeutung |
|---|---|
| Ausschalten | Ziele (Entities, Geräte, Bereiche, Labels, auch Szenen und Skripte), die ausgeschaltet werden |
| Einschalten | Ziele, die eingeschaltet werden |
| Lichter | Lichter, die mit **Farbe** und **Helligkeit** eingeschaltet werden. Hast du Lichter gewählt, kommt eine Seite, auf der du für jedes Licht die Farbe mit dem Farbwähler und die Helligkeit in Prozent einstellst. Ein leeres Feld heißt: das Licht geht so an, wie es zuletzt war. Die Werte werden gespeichert. Ein Weiß oder Grau (Sättigung unter 10 %) geht als **Farbtemperatur** an die Lampe, nicht als Farbe. Erst geht das Licht mit der Helligkeit an, nach 2 Sekunden folgt die Farbe als eigener Befehl, weil manche Lampen (zum Beispiel Matter-Lampen) die Farbe sonst nicht übernehmen |
| Rollo, Winde | eine Entity vom Typ `cover`, die auf eine Position (0 bis 100 %) fährt. Standard: 50 % bei „Saison vorbereiten“, 0 % bei „Saison beenden“. Die Position, die ein Ort hat, fährt diese Winde erst beim Start einer Aufnahme an (siehe oben, Schritt 3 des Ortes) |

Nur **Saison beenden** hat vier Felder mehr, für eine Winde, die erst ausgehen darf, wenn sie oben angekommen ist:

| Feld | Bedeutung |
|---|---|
| Winde hochfahren: Schalter „hoch“ | der Schalter `hoch` der Winde. Ist er und der Endschalter gewählt, fährt „Saison beenden“ die Winde mit diesem Schalter **bis zum Endschalter** (wie dein Skript beim Kalibrieren), statt über die Position. Die Zeitschätzung allein lässt die Winde oft knapp vor dem Endschalter stehen, dann geht der Endschalter nie an. Der Schalter geht danach wieder aus. Steht die Winde schon oben (Endschalter an), fährt sie nicht |
| Ausschalten, sobald der Endschalter an ist | Ziele, die erst ausgeschaltet werden, wenn der Endschalter an ist, zum Beispiel der Strom der Winde |
| Endschalter | eine Entity (`binary_sensor`, `switch` oder `input_boolean`), die „an“ ist, sobald die Winde oben ist, zum Beispiel „Endschalter oben“ |
| Höchstens warten | so viele Sekunden wartet die Taste auf den Endschalter (Standard 120) |

**Reihenfolge bei Saison vorbereiten:** Ziele ausschalten → Ziele einschalten → Lichter einschalten → Rollo oder Winde fahren (immer auf die Position von *Konfigurieren*).

**Reihenfolge bei Saison beenden:** Rollo oder Winde fahren (sie braucht dafür noch Strom) → Ziele ausschalten → Ziele einschalten → Lichter einschalten → auf den Endschalter warten → erst dann die Ziele „Ausschalten, sobald der Endschalter an ist“ ausschalten. Ist der Endschalter nach der Wartezeit nicht an, bleiben diese Ziele an, die Taste meldet einen Fehler und der Status-Sensor zeigt „Fehler“. Ohne gewählten Endschalter werden sie sofort ausgeschaltet. **Saison beenden** geht nur, solange bei dem Spielpartner keine Aufnahme läuft, und meldet das Ereignis `spiel_saison_season_ended` (bei einem Fehler meldet sie es nicht). Beide Tasten laufen auch bei Spielpartnern ohne Ort.

### Saison

Eine Saison ist ein Spieltag mit allen Aufnahmen darin, sie bekommt einen eigenen Ordner. **Pro Tag beginnt höchstens eine Saison:** Das **Saison vorbereiten** (Taste oder Dienst) beginnt die neue Saison, wenn es für heute noch keine gibt, **legt den Ordner der Saison im Zielordner an, falls es ihn dort noch nicht gibt**, und führt danach die Einstellungen von „Saison vorbereiten“ aus. Drückst du am selben Tag noch einmal auf Saison vorbereiten, bleibt die Saison, wie sie ist, der Ordner bleibt, und nur die Einstellungen laufen noch einmal. Läuft bei dem Spielpartner gerade eine Aufnahme, ändert Saison vorbereiten die Saison nie. Ist die App nicht erreichbar, läuft Saison vorbereiten trotzdem, nur der Ordner fehlt dann noch (die Aufnahme legt ihn beim Start ohnehin an). Wer eine Saison von Hand beginnen will, nimmt den Dienst `new_season`, für den dieselbe Regel gilt (er antwortet mit `started: false`, wenn es für heute schon eine Saison gibt). Eine Taste dafür gibt es nicht. **Saison beenden** (Taste oder Dienst `end_season`) führt die Einstellungen von „Saison beenden“ aus und meldet das Ereignis `spiel_saison_season_ended`; Saison-Nummer, Zähler und Auswertung bleiben, wie sie sind, und Saison vorbereiten beginnt am nächsten Tag die nächste Saison. Ein neu angelegter Spielpartner startet mit Saison 1 am Tag des Anlegens, die nächste beginnt also frühestens am Folgetag.

## Entities

Am Gerät **Spiel Saison**: Sensor **Status** (Bereit, Nimmt auf, Fehler), **Aktueller Spielpartner**, **Recorder: freier Speicher**, Binärsensoren **Recorder erreichbar** und **Aufnahme**.

Am Gerät **Spielpartner …**:

| Entity | Bedeutung |
|---|---|
| Schalter **Aufnahme** | an = Ablauf starten, aus = Aufnahme beenden |
| Auswahl **Ort** | der Ort für die Aufnahme. Es stehen nur die Orte dieses Spielpartners zur Wahl, die anderen erscheinen hier nicht (nur mit mindestens einem Ort) |
| Taste **Saison vorbereiten** | beginnt die Saison des Tages (nur wenn es für heute noch keine gibt), legt den Ordner der Saison an, falls er fehlt, und führt die Einstellungen von „Saison vorbereiten“ aus (*Konfigurieren*) |
| Taste **Saison beenden** | führt die Einstellungen von „Saison beenden“ aus (*Konfigurieren*, nicht während einer Aufnahme) und meldet das Ereignis `spiel_saison_season_ended` |
| Taste **Beleuchtung speichern** | speichert die Beleuchtung der Lichter des Spielpartners, wie sie jetzt ist (an oder aus, Farbe, Helligkeit), für die erste Aufnahme jeder Saison |
| Lichter, eines je Licht aus **Lichter** beim Spielpartner | Regler für die echte Lampe: zeigt ihren Zustand und gibt Ein/Aus, Helligkeit und Farbe an sie weiter |
| Sensoren **Saison**, **Aufnahmen in Saison**, **Aufnahmen gesamt** | Zähler. Ändern kannst du sie nur bei *Spielpartner bearbeiten* |
| Sensor **Status** | Bereit, Startet, Nimmt auf, Beendet. Attribute: Ort, Beginn, Aufnahmenummer, Kameras, `nickname`, `likes`, `treats` |
| Sensoren **Aufnahmedauer in Saison / gesamt** (Stunden), **Längste Aufnahme in Saison** (Minuten), **Letzte Aufnahme** | Auswertung |
| Sensor **Zielordner** | Ordner der laufenden oder nächsten Aufnahme, der volle Pfad steht im Attribut `path` |

Die Zähler zählen eine Aufnahme, sobald die App sie gestartet hat.

## Dienste und Ereignisse

| Dienst | Felder |
|---|---|
| `spiel_saison.start_recording` | `partner`, optional `place` (ein Ort dieses Spielpartners, sonst der am Spielpartner gewählte) |
| `spiel_saison.stop_recording` | `partner` |
| `spiel_saison.prepare` | `partner`, optional `place` (füllt nur den Platzhalter `{ort}` der Ordner-Muster); beginnt auch die Saison des Tages und legt ihren Ordner an (Antwort: `season`, `new_season`, `folder`, `folder_created`) |
| `spiel_saison.end_season` | `partner`; führt die Einstellungen von „Saison beenden“ aus, nur ohne laufende Aufnahme (Antwort: `season`, `recordings`, `seconds`) |
| `spiel_saison.save_lighting` | `partner`; speichert die Beleuchtung des Spielpartners, wie sie jetzt ist (Antwort: `lights`, `on`) |
| `spiel_saison.new_season` | `partner`; Saison +1, Zähler und Auswertung der Saison auf null, die alte Saison wird gespeichert. Einmal pro Tag und nur ohne laufende Aufnahme (Antwort: `started`, bei `false` mit `reason: already_today`) |

`partner` und `place` sind die Namen, Groß- und Kleinschreibung und Sonderzeichen sind egal. Die Dienste liefern eine Antwort mit dem Ergebnis.

Ereignisse für eigene Automationen (zum Beispiel Benachrichtigungen):

| Ereignis | Daten |
|---|---|
| `spiel_saison_recording_started` | `partner`, `nickname`, `partner_id`, `season`, `recording`, `place`, `folder`, `cams` |
| `spiel_saison_recording_stopped` | `partner`, `nickname`, `partner_id`, `season`, `recording`, `place`, `reason` (`manual`, `cancelled`, vom Recorder gemeldeter Grund), `duration_s` |
| `spiel_saison_season_started` | `partner`, `partner_id`, `season`, `start` |
| `spiel_saison_season_ended` | `partner`, `nickname`, `partner_id`, `season`, `start`, `recordings`, `seconds` |

Beendet der Recorder eine Aufnahme von sich aus (zum Beispiel weil der Speicher voll ist), räumt die Integration auf, sendet das Stopp-Ereignis und legt unter *Einstellungen → System → Reparaturen* einen Hinweis an.

## Von „Spiel Saison aufnehmen“ umsteigen

Zähler werden nicht übernommen, sie beginnen bei null. Setze sie bei Bedarf bei *Spielpartner bearbeiten*. Automationen, die die alten Tasten und Entities benutzen, stellst du auf die Taste **Saison vorbereiten** (sie beginnt auch die Saison des Tages und legt ihren Ordner an), den Schalter **Aufnahme** des Spielpartners und die **Ort**-Auswahl an seinem Gerät um, danach kannst du die alte Integration und die alte App entfernen.

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

**Spiel Saison** manages seasons, play partners and recordings in Home Assistant. Every play partner has fixed cameras, their own seasons and counters. One switch sets up a place (move the Ctronics cameras to a PTZ preset, switch lights and devices, make an announcement), waits for the lead time and starts a stream-copy recording through the app **Spiel Saison Recorder** (ffmpeg `-c copy`, no re-encoding). When you stop, it waits, puts everything back and updates the statistics. Every place belongs to a play partner and brings its cameras, with a PTZ position per Ctronics camera taken from the camera's stored positions; the announcement belongs to the play partner, together with an optional nickname (a placeholder for the announcements) and two lists of preferences that you type in directly; every preference gets a diagnostic sensor on the device, and preferences for working also get an “included” switch and a counter. The buttons **Prepare season** and **End season** have their own settings under *Configure* of the integration (the same for all places, not used by the recording): targets to turn off and on, lights with a colour and brightness you set, and a cover or winch position (a place can have a winch position that is approached when a recording starts); “End season” can also wait for an end switch before it turns off the winch; preparing also begins the season of the day, at most one season begins per day. Folder and file names come from patterns with placeholders for partner, season, recording number and camera prefix. The integration offers services and events for your own automations. Install the integration through HACS and add this repository to the App store for the recorder. The texts in the UI are available in German and English.

## Lizenz

[MIT](LICENSE)
