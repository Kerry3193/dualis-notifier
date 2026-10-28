# Dualis Notifier

Der Notifier prüft in Dualis veröffentlichte Modulnoten und sendet neue oder geänderte Ergebnisse an einen Discord-Webhook. Er nutzt den Login für `@student.dhbw-mannheim.de`.

Notenstand und ausstehende Nachrichten werden gemeinsam gespeichert. Bei einem fehlgeschlagenen Versand bleibt die Nachricht erhalten und wird in einem späteren Lauf erneut versucht. Der erste erfolgreiche Abruf legt nur einen Ausgangsstand an und versendet keine vorhandenen Noten.

## Einrichtung ohne Docker

Voraussetzung: Python 3.10 oder neuer. Python 3.10, 3.12 und 3.14 werden in CI geprüft.

```bash
git clone https://github.com/Kerry3193/dualis-notifier.git
cd dualis-notifier
python3 -m venv .venv
./.venv/bin/python -m pip install --require-hashes -r requirements.txt
cp .env.example .env
chmod 600 .env
nano .env
```

Die drei Pflichtwerte in `.env` ausfüllen:

```env
DUALIS_USER=s123456
DUALIS_PASSWD='dein-passwort'
DISCORD_WEBHOOK=https://discord.com/api/webhooks/WEBHOOK-ID/WEBHOOK-TOKEN
SEMESTER_ID=
```

Nur die Benutzerkennung ohne Domain eintragen. Passwörter werden unverändert übertragen; Formularkodierung übernimmt Requests. Für Passwörter mit Leerzeichen oder `#` den Wert in Anführungszeichen setzen und eingebettete Anführungszeichen nach der dotenv-Syntax schreiben. `${...}` wird als wörtlicher Passwortbestandteil behandelt. Bestehende Umgebungsvariablen haben Vorrang vor `.env`.

Die Webhook-URL muss eine Discord-HTTPS-URL mit numerischer Webhook-ID sein. Ein optionales `thread_id` bleibt erhalten; der Notifier setzt `wait=true` zur Versandbestätigung.

Einmal prüfen:

```bash
./.venv/bin/python dualis_notifier.py
```

Beim ersten Abruf erscheint „Ausgangsstand angelegt“. Fehlende Konfiguration wird vor einem Netzwerkaufruf gemeldet. Die Exit-Codes sind `0` für eine erfolgreiche Prüfung beziehungsweise eine übersprungene parallele Prüfung, `1` bei Abruf-/Speicher-/Versandfehlern oder ausstehenden Nachrichten und `2` bei ungültiger Konfiguration.

## Automatisch prüfen

Ein fortlaufender Prozess prüft sofort und anschließend standardmäßig 900 Sekunden nach Abschluss des vorherigen Laufs:

```bash
./.venv/bin/python -u dualis_notifier.py --watch
```

Alternativ eine Crontab für Prüfungen alle 15 Minuten von 06:00 bis 19:45 Uhr einrichten:

```cron
*/15 6-19 * * * cd "$HOME/dualis-notifier" && ./.venv/bin/python dualis_notifier.py >> "$HOME/dualis-notifier/notifier.log" 2>&1
```

Den Pfad an die eigene Installation anpassen. Die Prozesssperre verhindert, dass Cron und ein bereits laufender Prozess gleichzeitig denselben Zustand verändern. Der fortlaufende Prozess verarbeitet SIGTERM und SIGINT und beendet sich nach dem aktuellen Lauf.

## Daten und Benachrichtigungen

```bash
./.venv/bin/python dualis_notifier.py --status
```

Der Befehl zeigt den Pfad zur CSV-Datei, zur Zustandsdatei und die Anzahl ausstehender Nachrichten. Er verwendet keine Netzwerkverbindung.

Standardmäßig liegen die Daten unter `data/<konto-und-semester-kennung>/` im Projektverzeichnis. Ein relativer `DATA_DIR` wird gegen das Projektverzeichnis aufgelöst und hängt nicht vom Arbeitsverzeichnis eines Cron-Jobs ab. Verschiedene Konten und Semester erhalten getrennte Zustände.

- `state.json`: maßgeblicher Notenstand mit ausstehenden Nachrichten; atomar ersetzt und auf POSIX-Systemen mit Dateirechten `600` angelegt.
- `grades.csv`: ebenfalls atomar geschriebener Export zum Ansehen. Änderungen an dieser Datei ändern den maßgeblichen Zustand nicht.
- `notifier.lock`: Betriebssystem-Prozesssperre; sie wird bei Prozessende automatisch freigegeben. Die Datei bleibt bestehen und darf während eines Laufs nicht entfernt werden.

Fehlende Module bleiben gespeichert. Erscheinen sie unverändert wieder, entsteht keine doppelte Nachricht. Module werden anhand ihrer Nummer zugeordnet; ein eindeutiger Name dient bei fehlender Nummer als Ersatz. Gleiche Namen mit verschiedenen Modulnummern bleiben getrennt.

Die HTML-Auswertung wählt genau eine Tabelle mit den Spalten `Name` und `Endnote`. Unlesbare Seiten, fehlende Pflichtspalten und mehrdeutige Tabellen werden abgelehnt, bevor der Notenstand aktualisiert wird. Dezimalkommas bleiben erhalten. Änderungen an Name, Endnote, Credits oder Status lösen Meldungen aus; eine reine numerische Formatänderung nicht. Vorübergehend fehlende optionale Spalten behalten ihren bisherigen Wert.

Pro ausstehender Nachricht gibt es höchstens einen Versandversuch je Lauf. Beim ersten Fehler wird die Versandserie abgebrochen. Die nächste frühestmögliche Wiederholung erfolgt nach einer wachsenden Wartezeit von einer Minute bis zu einer Stunde; die tatsächliche Prüfung erfolgt im eingerichteten Intervall. Bei HTTP 429 wird mindestens die von Discord vorgegebene Wartezeit eingehalten. Ausstehende Nachrichten werden auch dann versucht, wenn der anschließende Dualis-Abruf fehlschlägt.

Nach einer Discord-Bestätigung wird jede Nachricht einzeln quittiert. Hat Discord eine Nachricht angenommen, aber ihre Antwort geht verloren oder der Prozess endet vor der Speicherung der Quittung, kann bei der Wiederholung eine doppelte Nachricht entstehen. Die Zustellung garantiert deshalb keine exakt einmalige Veröffentlichung.

## Bisherigen CSV-Cache übernehmen

Vor dem ersten Lauf der neuen Version kann der bisherige Cache ausdrücklich importiert werden:

```bash
./.venv/bin/python dualis_notifier.py --import-cache grades.csv
```

Die Quelldatei bleibt erhalten. Ein Import wird abgelehnt, wenn für das konfigurierte Konto/Semester bereits ein Zustand existiert. Ohne Import legt der erste Abruf einen neuen Ausgangsstand an. Vor einem Update das gesamte Datenverzeichnis sichern; bei einem Downgrade gehen ausstehende Nachrichten nicht in das alte CSV-Format über.

## Konfiguration

| Variable | Standard / Bedeutung |
|---|---|
| `DUALIS_USER` | Pflicht: Benutzerkennung ohne Domain |
| `DUALIS_PASSWD` | Pflicht: Passwort |
| `DISCORD_WEBHOOK` | Pflicht: Discord-Webhook-URL |
| `SEMESTER_ID` | Leer: Ergebnisse ohne Semestereinschränkung; sonst beispielsweise `-N000000015178000` |
| `DATA_DIR` | `data`, im Docker-Container `/data` |
| `AGENT_NAME` | `Dualis Notifier` |
| `CONNECT_TIMEOUT` | `5` Sekunden Verbindungszeitlimit |
| `READ_TIMEOUT` | `30` Sekunden Wartezeit auf weitere Antwortdaten |
| `CHECK_INTERVAL` | `900` Sekunden zwischen abgeschlossenen Läufen mit `--watch` |

Die Semester-ID steht in der URL einer Ergebnisseite gegebenenfalls nach `-N000307,`. Ein Lesezeitlimit ist kein Zeitlimit für den gesamten Download. Sitzungs-URLs mit `ARGUMENTS=-N…` nicht teilen.

## Docker

Mit ausgefüllter `.env` starten:

```bash
docker compose up -d --build
docker compose logs -f notifier
docker compose exec notifier python dualis_notifier.py --status
```

Der Prozess läuft als Benutzer ohne Root-Rechte, prüft beim Start sofort und schreibt Logs nach stdout/stderr. Das benannte Volume `notifier-data` speichert den Zustand über Container-Neuerstellungen hinweg. `docker compose down` behält es; `docker compose down -v` entfernt die gespeicherten Daten.

Alternativ ohne Compose:

```bash
docker build -t dualis-notifier .
docker run -d --name dualis-notifier --restart unless-stopped \
  --env-file .env -e DATA_DIR=/data \
  -v dualis-notifier-data:/data \
  dualis-notifier
```

Für einen bestehenden Cache den Pfad vor dem ersten Abruf einbinden und importieren, zum Beispiel mit Compose:

```bash
docker compose run --rm -v "$PWD/grades.csv:/legacy-grades.csv:ro" notifier --import-cache /legacy-grades.csv
docker compose up -d
```

Ein Bind-Mount statt eines benannten Volumes muss für UID/GID `10001` schreibbar sein. `.env`, lokale Noten, Logs und Tests werden nicht in das Docker-Image kopiert. Zugangsdaten werden nicht in `/etc/environment` geschrieben.

## Tests und Wartung

```bash
./.venv/bin/python -m unittest discover -s test -v
./.venv/bin/python -m pip check
```

Die Tests verwenden synthetische HTML-Daten und simulierte HTTP-Antworten. Sie benötigen weder Dualis-Zugangsdaten noch einen echten Discord-Kanal. Die CI prüft Python 3.10, 3.12 und 3.14 sowie Docker-Build, Ausführung ohne Root-Rechte und Datenpersistenz zwischen zwei Containern.

`requirements.in` enthält direkte Abhängigkeiten. `requirements.txt` sperrt auch indirekte Abhängigkeiten mit Versionsnummern und SHA-256-Prüfsummen. Zum Aktualisieren mit installiertem uv:

```bash
uv pip compile --universal --python-version 3.10 --generate-hashes requirements.in -o requirements.txt
./.venv/bin/python -m pip install --require-hashes -r requirements.txt
./.venv/bin/python -m unittest discover -s test -v
```

Dependabot schlägt regelmäßige Änderungen an Python-Paketen, GitHub Actions und dem Docker-Basisimage vor. Änderungen erst nach Prüfung der Release Notes und Tests übernehmen.

## Sicherheit und Fehlerbehandlung

Passwort und Webhook-URL gehören ausschließlich in `.env` oder die Laufzeitumgebung. `.env`, `data/` und lokale Exporte werden von Git ignoriert. Der Notifier schreibt keine rohe Dualis-HTML-Seite auf die Festplatte und protokolliert keine geheimen URLs oder Requests-Ausnahmetexte. Beschädigte Zustandsdateien werden gemeldet und nicht durch einen leeren Ausgangsstand ersetzt.

Für einen produktiven Test müssen die aktuelle Dualis-Anmeldung und eine echte Discord-Zustellung mit den eigenen Zugangsdaten geprüft werden. Die synthetischen Tests beweisen diese externen Vertragsdetails nicht.
