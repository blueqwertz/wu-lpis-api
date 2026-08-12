# WU LPIS API

Eine Python API für das Lehrveranstaltungs- und Prüfungsinformationssystem (LPIS) der WU Wien "[LPIS](https://www.wu.ac.at/studierende/tools-services/lpis/)". Die API verwendet `python.mechanize` für das emulieren eines Webbrowser, zum Navigieren und Absenden von (Form) Requests

## Desktop-App

Neben der Kommandozeile gibt es eine Tkinter-Oberfläche:

```
python gui/main.py
```

Lehrveranstaltungen durchsuchen, Anmeldung vorbereiten und starten, Noten
ansehen – inklusive 2FA im Fenster. Sie lässt sich mit PyInstaller zu einer
`.exe` bzw. `.app` bauen. Details in [gui/README.md](gui/README.md).

## Benutzerverwaltung

Alle Zugänge stehen in `users.json` im Hauptverzeichnis. Die Datei ist normales
JSON und kann auch von Hand bearbeitet werden:

```json
{
  "default": "max",
  "users": [
    {
      "name": "max",
      "username": "h12345678",
      "password": "",
      "msdomain": "s.wu.ac.at",
      "mfa_method": "",
      "sectionpoint": "",
      "planobject": "",
      "course": "",
      "course2": "",
      "offset": 0.7
    }
  ]
}
```

`name` ist der Kurzname für `--user`, alles andere entspricht den gleichnamigen
Parametern. Ein leeres `password` bedeutet, dass es bei Bedarf abgefragt wird –
nach dem ersten Login genügt ohnehin die gespeicherte Session.

| Befehl | Wirkung |
| --- | --- |
| `python api.py --users` | Interaktive Verwaltung: anlegen, ändern, löschen, Standard setzen |
| `python api.py --list-users` | Gespeicherte Benutzer anzeigen |
| `python api.py --user max ...` | Diesen Benutzer verwenden |
| `python api.py -u h12345678 -p geheim --save-user --save-password` | Aktuelle Angaben als Profil speichern |

Ohne `--user` wird der mit `*` markierte Standardbenutzer verwendet. In der
Desktop-App macht das der Knopf *Verwalten …*; beide arbeiten auf derselben
Datei.

Da die Datei Passwörter enthalten kann, wird sie mit Zugriff nur für den
eigenen Benutzer geschrieben und ist in `.gitignore` eingetragen.

### Umstieg von den `.credentials`-Dateien

Früher lag jeder Zugang in einer eigenen Datei `.credentials` bzw.
`.credentials-<name>`. Diese Dateien werden beim ersten Start der neuen Version
automatisch nach `users.json` übernommen – aus `.credentials-max` wird das
Profil `max`. Es ist nichts zu tun.

Die Originaldateien bleiben liegen, damit bestehende Cronjobs mit
`--credfile .credentials-max` weiterlaufen. Gelesen werden sie nicht mehr:
Änderungen gehören ab jetzt in `users.json`. Aufräumen lassen sie sich in
`python api.py --users` bzw. in der Benutzerverwaltung der App. Jede Datei wird
nur einmal übernommen, ein später gelöschtes Profil kommt also nicht zurück.

## Authentifizierung

LPIS hat seit 2026 kein eigenes Login-Formular mehr. Der Login läuft über
Keycloak (`bach-id.wu.ac.at`) und von dort zwingend weiter über Microsoft
Entra ID (`login.microsoftonline.com`) inklusive 2FA. Die Keycloak-Loginseite
bietet ausschließlich den Identity Provider `wu-microsoft` an, ein lokales
Passwortformular existiert nicht.

Die Zugangsdaten kommen aus der [Benutzerverwaltung](#benutzerverwaltung)
(`--user`), können aber weiterhin über `--username`/`--password` oder ein
Credentials File mit `--credfile` übergeben werden.

Das credfile muss folgendes Format aufweisen.

```
username=_USER_
password=_PASS_
```

Optional zusätzlich:

```
msdomain=s.wu.ac.at
mfa_method=PhoneAppNotification
```

`username` ist der LPIS-Benutzername (z.B. `h12345678`). Für den
Microsoft-Login wird `msdomain` angehängt (Default `s.wu.ac.at`), also
`h12345678@s.wu.ac.at`. Wer eine andere Domain braucht, setzt `--msdomain`.

### 2FA

Nach Benutzername und Passwort verlangt Microsoft den zweiten Faktor. Default
ist die Authenticator-Push-Benachrichtigung mit Number Matching: die API zeigt
eine zweistellige Zahl an, die am Handy in der Authenticator-App eingegeben
werden muss, und pollt anschließend bis zur Bestätigung.

Andere Methoden lassen sich mit `--mfa-method` erzwingen:

| Methode | Ablauf |
| --- | --- |
| `PhoneAppNotification` | Push, Zahl am Handy eingeben (Default) |
| `PhoneAppOTP` | Code aus der Authenticator-App im Terminal eingeben |
| `OneWaySMS` | Code aus der SMS im Terminal eingeben |

### Session-Speicherung

Der erste Login braucht ein interaktives Terminal (2FA-Bestätigung). Danach
läuft alles unbeaufsichtigt, z.B. per cron: ohne Terminal werden Rückfragen
übersprungen (`--sectionpoint` fällt auf den ersten Eintrag zurück) und ein
nötig gewordener 2FA-Login bricht mit einer klaren Meldung ab statt mit einem
Traceback.

Nach erfolgreichem Login werden die Cookies (inkl. des persistenten
Microsoft-Session-Cookies) nach `sessions/<username>` geschrieben. Bei den
folgenden Läufen wird die gesamte Redirect-Kette damit ohne Passwort- und
ohne 2FA-Abfrage durchlaufen. Der Ablageort lässt sich mit `--sessiondir`
ändern. Ist die gespeicherte Session ungültig, wird sie verworfen und
automatisch ein vollständiger Login gestartet.

# Copyright & License

Copyright (c) 2018-2019 Alexander Hofstätter - Released under the [MIT license](LICENSE.md).
