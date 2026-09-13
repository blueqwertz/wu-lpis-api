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

## Testen neben einem produktiven Checkout

Beim Start prüft die API auf Updates und überschreibt bei einer neueren Version
auf GitHub **alle** Dateien im Verzeichnis mit dem Release-Stand. In einem
Worktree mit noch nicht gemergten Änderungen ist das fatal: der Testcode wäre
weg und der Lauf würde mit dem Release-Stand weiterlaufen, ohne dass man es
merkt. Deshalb dort immer:

```
export LPIS_SKIP_UPDATE=1
```

Nicht im Git und daher pro Worktree neu anzulegen: `.credentials` und
`sessions/`. Ein frischer Worktree hat keine gespeicherte Session, der erste
Login braucht also einmal ein interaktives Terminal für 2FA. Alternativ das
Sessionfile aus dem produktiven Checkout **kopieren** (nicht verlinken) oder
mit `--sessiondir` auf ein eigenes Verzeichnis zeigen - zwei gleichzeitig
laufende Prozesse, die sich dasselbe Sessionfile teilen, überschreiben sich
gegenseitig die Cookies.

## Anmeldung

Die Anmeldung zielt nicht mehr auf einen einzelnen Zeitpunkt. Statt zu raten,
wie lange ein Request zum LPIS unterwegs ist, werden ab einigen Sekunden vor
der Öffnung laufend Requests auf einem festen Raster abgeschickt, parallel und
über eigene, schon geöffnete Verbindungen. Die erste Antwort, die die LV als
anmeldbar zeigt, gewinnt; danach werden keine weiteren Requests mehr
abgeschickt und noch laufende Antworten verworfen. Mit dieser Antwort wird
dann angemeldet.

Der Sinn: ein einzelner sequenzieller Poll-Loop bekommt pro Round-Trip genau
einen Versuch, und ob er trifft, hängt davon ab, wo seine Versuche zufällig
relativ zur Öffnungssekunde liegen. Beim Raster ist dagegen immer ein Request
unterwegs, egal wie lang der Round-Trip gerade ist.

| Option | Default | Bedeutung |
| --- | --- | --- |
| `--burst-lead` | `3` | Sekunden vor der Öffnung, ab denen gefeuert wird |
| `--burst-interval` | `0.1` | Abstand zwischen zwei Requests |
| `--burst-deadline` | `10` | Sekunden nach der Öffnung, nach denen aufgegeben wird |
| `--burst-workers` | auto | Anzahl paralleler Verbindungen (auto = `burst-lead / burst-interval`, mit Defaults also 30) |

`--offset` wird nicht mehr verwendet (die Option bleibt nur erhalten, damit
bestehende Cron-Einträge weiter laufen); wird sie gesetzt, weist das Log darauf
hin.

Die Requests vor der Öffnungssekunde sind der eigentliche Sinn der Sache, nicht
Verschnitt: das LPIS braucht unter Last 1-15 Sekunden, um einen Request zu
verarbeiten. Ein Request, der eine Sekunde vor Punkt rausgeht, wird also oft
erst nach Punkt verarbeitet - und dann ist die Anmeldung offen. Genau deshalb
müssen sie alle gleichzeitig unterwegs sein, weshalb `--burst-workers`
standardmäßig so gewählt wird, dass der komplette Vorlauf parallel läuft.

Wer den Vorlauf verlängert, muss die Verbindungen mitziehen: `--burst-lead 10`
bei `--burst-interval 0.1` bräuchte 100 parallele Verbindungen. Die Automatik
deckelt bei 64 und sagt es im Log.

Das Log schreibt für jeden Request `planned` (Rasterzeitpunkt) und `sent`
(tatsächliches Absenden) relativ zur Öffnungssekunde mit. Laufen die beiden
auseinander, kam das Raster nicht mehr nach.

# Copyright & License

Copyright (c) 2018-2019 Alexander Hofstätter - Released under the [MIT license](LICENSE.md).
