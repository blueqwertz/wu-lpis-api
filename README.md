# WU LPIS API

Eine Python API für das Lehrveranstaltungs- und Prüfungsinformationssystem (LPIS) der WU Wien "[LPIS](https://www.wu.ac.at/studierende/tools-services/lpis/)". Die API verwendet `python.mechanize` für das emulieren eines Webbrowser, zum Navigieren und Absenden von (Form) Requests

## Authentifizierung

Entweder über die parameter `--username` und `--password` die Zugangsdaten übermitteln, oder alternativ ein Credentials File mit `--credfile` angeben.

Das credfile muss folgendes Format aufweisen.

```
username=_USER_
password=_PASS_
```

# Copyright & License

Copyright (c) 2018-2019 Alexander Hofstätter - Released under the [MIT license](LICENSE.md).
