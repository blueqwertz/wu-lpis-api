# Desktop-App

Eine schlichte Tkinter-Oberfläche für die LPIS-API. Sie verwendet ausschließlich
`ttk`-Widgets ohne eigenes Styling, damit sie unter Windows und macOS aussieht
wie ein normales Programm des jeweiligen Systems.

## Starten

```bash
python gui/main.py
```

oder

```bash
python -m gui
```

Voraussetzung sind die Pakete aus `requirements.txt` sowie Tkinter. Unter
Windows ist Tkinter im Python-Installer enthalten. Unter macOS sollte die
Python-Version von [python.org](https://www.python.org/downloads/macos/) oder
`brew install python-tk` verwendet werden – das mitgelieferte System-Python hat
ein sehr altes Tk.

## Aufbau

| Tab | Inhalt |
| --- | --- |
| Lehrveranstaltungen | Studienplanpunkte und LVs als Baum, mit freien Plätzen und Anmeldezeitraum. Doppelklick auf eine LV übernimmt sie in den Anmelde-Tab. |
| Anmeldung | Studienplanpunkt, LV-Nummer, Ausweich-LV und Vorlauf. Das Programm wartet bis zum Anmeldebeginn und schickt die Anmeldung dann ab. |
| Noten | Notenliste und die ECTS-gewichteten Durchschnitte. |
| Protokoll | Die vollständige Ausgabe, wie sie sonst im Terminal steht. |

Der 2FA-Ablauf läuft komplett im Fenster: Die Zahl für das Number Matching
erscheint in einem eigenen Fenster, ein Code (`PhoneAppOTP`, `OneWaySMS`) wird
in einem Dialog abgefragt.

Lange Aktionen laufen in einem Hintergrund-Thread, das Fenster bleibt also
bedienbar. *Abbrechen* wirkt sofort – auch mitten im Warten auf den
Anmeldebeginn.

## Benutzerverwaltung

Über *Verwalten …* im Zugangsbereich. Die Profile liegen in `users.json` und
sind dieselben, die das Kommandozeilenprogramm mit `--user` verwendet – siehe
[README.md](../README.md#benutzerverwaltung).

## Ordner

Aus dem Quellcode gestartet schreibt die App `logs/`, `sessions/` und
`users.json` in das Projektverzeichnis, teilt sich also alles mit `api.py`.
Eine gebaute App darf nicht in ihr eigenes Bundle schreiben und verwendet
stattdessen

* Windows: `%LOCALAPPDATA%\wu-lpis-api`
* macOS: `~/Library/Application Support/wu-lpis-api`

## Executable bauen

```bash
python -m pip install -r gui/requirements.txt
python gui/build.py
```

Ergebnis ist `dist/WU-LPIS.exe` (Windows) bzw. `dist/WU-LPIS.app` (macOS).
PyInstaller kann nicht cross-kompilieren: Die Windows-Datei muss unter Windows
gebaut werden, die macOS-App unter macOS.

Hinweise:

* Die App ist nicht signiert. Windows SmartScreen bzw. macOS Gatekeeper melden
  sich beim ersten Start ("Mehr Informationen" → "Trotzdem ausführen", unter
  macOS Rechtsklick → "Öffnen").
* Auf Apple Silicon gebaute Apps laufen nur auf Apple Silicon. Für eine
  universelle App muss mit einem Universal2-Python gebaut werden.
* Die gebaute App aktualisiert sich nicht selbst; sie weist nur darauf hin,
  wenn auf GitHub eine neuere Version steht.

## Struktur

| Datei | Aufgabe |
| --- | --- |
| `main.py` | Einstiegspunkt: Pfade, Ausgabeumleitung, Fenster |
| `runtime.py` | Arbeitsverzeichnis, Logdatei und `stdout`/`stderr`-Umleitung (muss vor den Kernmodulen laufen) |
| `lpis.py` | **Der eigene LPIS-Client**: Login, Navigation, Parsing, Notenschnitt, abbrechbare Anmeldung |
| `worker.py` | Hintergrund-Thread, der die Aktionen ausführt |
| `bridge.py` | Warteschlange zwischen Worker-Thread und Tk, plus die Prompt-Schnittstelle für den Login |
| `app.py` | Hauptfenster |
| `dialogs.py` | 2FA-Fenster und Benutzerverwaltung |
| `build.py`, `wulpis.spec` | PyInstaller |

## Kein Wrapper um die CLI

Die App bringt ihre LPIS-Logik selbst mit. `gui/lpis.py` navigiert, parst die
Seiten in Objekte (`PlanPoint`, `Course`, `Grade`) und rechnet die
ECTS-gewichteten Schnitte selbst aus. Nichts davon druckt, fragt auf einem
Terminal oder blockiert ohne Ausweg – jedes Warten lauscht auf ein
`threading.Event`, ein Abbruch wirkt also sofort.

`WuLpisApiClass.py` und `api.py` werden von der GUI nicht importiert; man kann
sie löschen, ohne dass die App etwas davon merkt. Geteilt wird nur, was
Bibliothek und nicht Darstellung ist:

| Modul | Wofür |
| --- | --- |
| `ms_login.py` | Die Login-Kette LPIS → Keycloak → Microsoft Entra inkl. 2FA. Die App reicht über den Parameter `ui` ihre eigenen Dialoge hinein (`bridge.PromptUI`) – ohne die Standardimplementierung `TerminalUI` zu berühren, die die CLI weiter verwendet. |
| `users.py` | Die gemeinsame Benutzerdatenbank, damit App und CLI dieselben Zugänge sehen. |
| `logger.py` | Dieselbe Logdatei `logs/output-<user>.log` wie die CLI. |
