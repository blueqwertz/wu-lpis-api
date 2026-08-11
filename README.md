# WU LPIS API

Eine Python API für das Lehrveranstaltungs- und Prüfungsinformationssystem (LPIS) der WU Wien "[LPIS](https://www.wu.ac.at/studierende/tools-services/lpis/)". Die API verwendet `python.mechanize` für das emulieren eines Webbrowser, zum Navigieren und Absenden von (Form) Requests

## Authentifizierung

LPIS hat seit 2026 kein eigenes Login-Formular mehr. Der Login läuft über
Keycloak (`bach-id.wu.ac.at`) und von dort zwingend weiter über Microsoft
Entra ID (`login.microsoftonline.com`) inklusive 2FA. Die Keycloak-Loginseite
bietet ausschließlich den Identity Provider `wu-microsoft` an, ein lokales
Passwortformular existiert nicht.

Entweder über die parameter `--username` und `--password` die Zugangsdaten übermitteln, oder alternativ ein Credentials File mit `--credfile` angeben.

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
