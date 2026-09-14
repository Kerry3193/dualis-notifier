# Dualis Notifier

`dualis-notifier` prüft deine in Dualis veröffentlichten Modulnoten und sendet bei Änderungen eine Nachricht an einen Discord-Webhook.

Die Noten werden lokal in `grades.csv` gespeichert. Bei jedem weiteren Lauf vergleicht das Script die Module über ihre Dualis-Modulnummer und benachrichtigt dich nur über neue oder tatsächlich geänderte Noten.

> Die Zugangsdaten liegen ausschließlich in deiner lokalen `.env`-Datei. Sie wird nicht in Git übernommen.

## Voraussetzungen

- Python 3.10 oder neuer
- Ein Dualis-Benutzername, z. B. `s123456`
- Ein Discord-Webhook für den gewünschten Kanal

## Einrichtung ohne Docker

Repository öffnen und virtuelle Python-Umgebung mit allen Abhängigkeiten einrichten:

```bash
cd ~/dualis-notifier
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
```

Dann die Konfigurationsvorlage kopieren:

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

Beispiel für den Inhalt von `.env`:

```env
# Nur die s-Kennung eintragen, ohne @student.dhbw-mannheim.de
DUALIS_USER=s123456
DUALIS_PASSWD=dein-dualis-passwort

# Leer lassen, um alle in der Dualis-Ansicht verfügbaren Ergebnisse abzurufen.
SEMESTER_ID=

# Webhook-URL aus den Discord-Kanal-Einstellungen
DISCORD_WEBHOOK=https://discord.com/api/webhooks/WEBHOOK-ID/WEBHOOK-TOKEN

# Optional
AGENT_NAME=Dualis Notifier
```

Die Datei wird beim Start automatisch geladen. Werte, die als normale Umgebungsvariablen gesetzt wurden, haben Vorrang – dadurch bleibt Docker ebenfalls unterstützt.

## Erster Testlauf

```bash
cd ~/dualis-notifier
./.venv/bin/python dualis_notifier.py
```

Beim ersten erfolgreichen Lauf erscheinen diese Meldungen:

```text
W: No cache found
I: Created cache
```

Das ist erwartetes Verhalten: Es wird lediglich `grades.csv` als Ausgangsstand angelegt. Es wird dabei noch keine Discord-Nachricht gesendet.

### Verhalten bei verschwundenen Modulen

Dualis kann Ergebnisse eines Moduls vorübergehend ausblenden und später erneut veröffentlichen. Das Script behält ein in einer Abfrage fehlendes Modul deshalb im Cache und markiert es nur mit einem internen Zeitstempel. Erscheint es danach mit unveränderten Daten erneut, wird **keine** doppelte Discord-Nachricht gesendet.

Es gibt nur diese Benachrichtigungen:

- **Neue Note**: ein bisher unbekanntes Modul erscheint nach dem ersten Lauf.
- **Note geändert**: die gespeicherten Daten eines bekannten Moduls haben sich geändert.

## Automatisch prüfen

Für eine Prüfung alle 15 Minuten von 06:00 bis 19:45 Uhr täglich, diese Crontab einrichten:

```bash
crontab -e
```

Folgende Zeile einfügen:

```cron
*/15 6-19 * * * cd "$HOME/dualis-notifier" && ./.venv/bin/python dualis_notifier.py >> "$HOME/dualis-notifier/notifier.log" 2>&1
```

Den eingerichteten Zeitplan anzeigen:

```bash
crontab -l
```

Live-Logs ansehen:

```bash
tail -f ~/dualis-notifier/notifier.log
```

## Abgerufene Noten ansehen

Die gespeicherten Noten stehen in `grades.csv`:

```bash
cd ~/dualis-notifier
./.venv/bin/python -c "import pandas as pd; print(pd.read_csv('grades.csv').to_string(index=False))"
```

## Semester-ID

Normalerweise kann `SEMESTER_ID` leer bleiben. Das Script ruft dann die Ergebnisse ab, die Dualis ohne Semestereinschränkung bereitstellt.

Wenn du auf ein bestimmtes Semester einschränken möchtest, übergib dessen Dualis-ID:

```env
SEMESTER_ID=-N000000015178000
```

Die ID steht – falls Dualis sie übergibt – in der Adresse einer Ergebnisseite direkt nach `-N000307,`.

## Docker (optional)

Das Projekt lässt sich auch als Container ausführen. Im Projektordner bauen und starten:

```bash
docker build -t dualis-notifier .
docker run -d --name dualis-notifier --restart unless-stopped \
  --env-file .env \
  dualis-notifier
```

Der Container prüft ebenfalls alle 15 Minuten.

## Sicherheit

- Teile weder deine Dualis-Zugangsdaten noch Discord-Webhook-URLs.
- Gib keine Dualis-URLs mit `ARGUMENTS=-N…` weiter; sie können eine temporäre Sitzungskennung enthalten.
- Die lokale `.env` ist per `.gitignore` vom Commit ausgeschlossen. Prüfe vor einem Commit trotzdem immer `git status`.
