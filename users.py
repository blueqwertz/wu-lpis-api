#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Stored user profiles, shared by the command line tool and the gui.

Until now every account lived in its own ``.credentials-<name>`` file in the
main directory. That works, but nothing can list those accounts, the format
carries no structure, and every tool has to know the naming convention.

Everything is kept in one json database instead - ``users.json`` in the main
directory, right where the credentials files were. Json because it is still a
plain text file that can be opened and edited by hand:

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

Existing ``.credentials*`` files are imported automatically on first use and
are left untouched afterwards, so a cron job that passes ``--credfile`` keeps
working exactly as before. Imported files are remembered by name so a profile
deleted later does not come back on the next start.

The database may contain passwords, so it is written with owner-only
permissions. Storing the password stays optional - the session cookie in
``sessions/`` already reduces it to a once-per-account thing.
"""

import glob
import json
import os
import re
import sys

FILENAME = "users.json"

CREDENTIALS_PREFIX = ".credentials"

# leftovers of editors and backups are not accounts
IGNORED_SUFFIXES = ("~", ".bak", ".swp", ".tmp", ".orig", ".example", ".sample")

# only these keys are read from a profile, so neither a hand edit nor an old
# credentials file can push unexpected values into the login
FIELDS = ("name", "username", "password", "msdomain", "mfa_method",
          "sessiondir", "sectionpoint", "planobject", "course", "course2",
          "offset")

DEFAULT_MSDOMAIN = "s.wu.ac.at"

MFA_METHODS = ("", "PhoneAppNotification", "PhoneAppOTP", "OneWaySMS")

NAME_PATTERN = re.compile(r"^[A-Za-z0-9._ -]{1,64}$")


def main_dir():
    """The main directory - where users.json and the old credentials files are.

    From source that is the repository, no matter where the tool was started
    from. A frozen gui cannot write into its own bundle and has already
    switched to its data directory, so there the current directory is used.
    """
    override = os.environ.get("WU_LPIS_HOME")
    if override:
        return override
    if getattr(sys, "frozen", False):
        return os.getcwd()
    return os.path.dirname(os.path.abspath(__file__))


def store_path():
    return os.environ.get("WU_LPIS_USERS") or os.path.join(main_dir(), FILENAME)


def _read_text(path):
    """Read a hand written file without tripping over its encoding.

    utf-8-sig also swallows the byte order mark that editors like Notepad put
    in front of the file - with it the first key would be unusable.
    """
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            with open(path, "r", encoding=encoding) as handle:
                return handle.read()
        except UnicodeDecodeError:
            continue
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def read_credentials_file(path, separator="="):
    """Read a ``key=value`` credentials file into a dict."""
    data = {}
    for line in _read_text(path).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if separator not in line:
            continue
        key, value = line.split(separator, 1)
        data[key.strip()] = value.strip()
    return data


class User(dict):
    """One account. A dict, so it is written out as it is read."""

    def __init__(self, username="", **values):
        super().__init__()
        self["name"] = ""
        self["username"] = (username or "").strip()
        self["password"] = ""
        self["msdomain"] = DEFAULT_MSDOMAIN
        self["mfa_method"] = ""
        self["sessiondir"] = ""
        self["sectionpoint"] = ""
        self["planobject"] = ""
        self["course"] = ""
        self["course2"] = ""
        self["offset"] = 0.7
        self.update({key: value for key, value in values.items()
                     if key in FIELDS and value is not None})
        if not self["name"]:
            self["name"] = self["username"]

    @property
    def name(self):
        return (self.get("name") or self.get("username") or "").strip()

    @property
    def username(self):
        return (self.get("username") or "").strip()

    @property
    def title(self):
        """What is shown in a list."""
        if self.name and self.name.lower() != self.username.lower():
            return "%s (%s)" % (self.name, self.username)
        return self.username

    def matches(self, wanted):
        wanted = (wanted or "").strip().lower()
        if not wanted:
            return False
        return wanted in (self.name.lower(), self.username.lower(), self.title.lower())

    def describe(self):
        """One line for the terminal - never contains the password itself."""
        parts = [self.title]
        parts.append("Passwort gespeichert" if self.get("password") else "kein Passwort")
        if (self.get("msdomain") or DEFAULT_MSDOMAIN) != DEFAULT_MSDOMAIN:
            parts.append("Domain %s" % self["msdomain"])
        if self.get("mfa_method"):
            parts.append("2FA %s" % self["mfa_method"])
        return " - ".join(parts)


class UserStore():
    """The json database: load, change, save."""

    def __init__(self, path=None, migrate=True):
        self.path = path or store_path()
        self.users = []
        self.default = ""
        self.migrated = []
        self.notes = []
        self.load()
        if migrate:
            self.import_credentials_files()

    # ------------------------------------------------------------------ #
    # file handling
    # ------------------------------------------------------------------ #

    def load(self):
        self.users = []
        self.default = ""
        self.migrated = []
        if not os.path.isfile(self.path):
            return self
        try:
            raw = json.loads(_read_text(self.path) or "{}")
        except (OSError, ValueError) as error:
            # a broken database must not stop a login: it is treated as empty,
            # but never silently overwritten - the note is shown to the user
            self.notes.append("users.json konnte nicht gelesen werden (%s)" % error)
            self.broken = True
            return self
        entries = raw.get("users") if isinstance(raw, dict) else raw
        for entry in entries or []:
            if isinstance(entry, dict) and (entry.get("username") or "").strip():
                self.users.append(User(**entry))
        if isinstance(raw, dict):
            self.default = raw.get("default") or ""
            self.migrated = list(raw.get("migrated") or [])
        if self.default and not self.get(self.default):
            self.default = ""
        return self

    def save(self):
        payload = {
            "default": self.default,
            "migrated": self.migrated,
            "users": [dict(user) for user in self.users],
        }
        directory = os.path.dirname(self.path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        try:
            # the database can hold passwords, so keep it to the owner
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        return self

    # ------------------------------------------------------------------ #
    # migration of the old .credentials files
    # ------------------------------------------------------------------ #

    def import_credentials_files(self, directory=None):
        """Take over ``.credentials`` / ``.credentials-<name>`` files.

        The files stay where they are - only their content is copied into the
        database, and each file is remembered so a profile that is deleted
        later does not reappear.
        """
        directory = directory or main_dir()
        added = []
        for path in sorted(glob.glob(os.path.join(directory, CREDENTIALS_PREFIX + "*"))):
            basename = os.path.basename(path)
            if not os.path.isfile(path) or basename in self.migrated:
                continue
            if basename.endswith(IGNORED_SUFFIXES):
                continue
            try:
                data = read_credentials_file(path)
            except OSError:
                continue
            username = (data.get("username") or "").strip()
            if not username:
                continue
            self.migrated.append(basename)
            if self.get(username):
                continue
            # ".credentials-max" becomes the profile "max", plain
            # ".credentials" is named after its username
            suffix = basename[len(CREDENTIALS_PREFIX):].lstrip("-.")
            user = User(**{key: value for key, value in data.items() if key in FIELDS})
            user["username"] = username
            user["name"] = suffix or username
            self.put(user)
            added.append(user)

        if added or self.migrated:
            try:
                self.save()
            except OSError as error:
                self.notes.append("users.json konnte nicht geschrieben werden (%s)" % error)
                return added
        if added:
            self.notes.append(
                "%d Zugangsdaten-Datei(en) nach %s uebernommen: %s"
                % (len(added), self.path, ", ".join(user.title for user in added)))
            self.notes.append(
                "die alten .credentials-Dateien werden nicht mehr gelesen und "
                "koennen geloescht werden")
        return added

    def leftover_credentials_files(self, directory=None):
        """Imported files that are still lying around, oldest convention first."""
        directory = directory or main_dir()
        found = []
        for basename in self.migrated:
            path = os.path.join(directory, basename)
            if os.path.isfile(path):
                found.append(path)
        return found

    def delete_credentials_files(self, paths):
        """Remove old credentials files after they were taken over."""
        removed = []
        for path in paths:
            try:
                os.remove(path)
                removed.append(path)
            except OSError as error:
                self.notes.append("%s konnte nicht geloescht werden (%s)"
                                  % (os.path.basename(path), error))
        return removed

    # ------------------------------------------------------------------ #
    # lookups
    # ------------------------------------------------------------------ #

    def __len__(self):
        return len(self.users)

    def __iter__(self):
        return iter(self.users)

    def names(self):
        return [user.name for user in self.users]

    def titles(self):
        return [user.title for user in self.users]

    def get(self, wanted):
        if not wanted:
            return None
        for user in self.users:
            if user.matches(wanted):
                return user
        return None

    def default_user(self):
        """The profile to use when none was named."""
        if self.default:
            user = self.get(self.default)
            if user:
                return user
        return self.users[0] if len(self.users) == 1 else None

    # ------------------------------------------------------------------ #
    # changes
    # ------------------------------------------------------------------ #

    def put(self, user):
        """Add a profile, or replace the one with the same name."""
        if not isinstance(user, User):
            user = User(**user)
        if not user.username:
            raise ValueError("ein Benutzername wird gebraucht")
        if not NAME_PATTERN.match(user.name):
            raise ValueError("ungueltige Bezeichnung: %r" % user.name)
        existing = self.get(user.name) or self.get(user.username)
        if existing is not None:
            self.users[self.users.index(existing)] = user
        else:
            self.users.append(user)
        if not self.default:
            self.default = user.name
        return user

    def remove(self, wanted):
        user = self.get(wanted)
        if user is None:
            return False
        self.users.remove(user)
        if (self.default or "").lower() == user.name.lower():
            self.default = self.users[0].name if self.users else ""
        return True

    def set_default(self, wanted):
        user = self.get(wanted)
        if user is None:
            return False
        self.default = user.name
        return True


# ---------------------------------------------------------------------- #
# terminal side of the user management
# ---------------------------------------------------------------------- #

def _has_tty():
    return sys.stdin.isatty() and sys.stdout.isatty()


def _ask(message, default=""):
    suffix = " [%s]" % default if default else ""
    try:
        answer = input("%s%s: " % (message, suffix)).strip()
    except (EOFError, KeyboardInterrupt):
        print("")
        return default
    return answer or default


def _ask_password(current=""):
    import getpass
    if current:
        print("Passwort ist gespeichert - Eingabe leer lassen, um es zu behalten.")
    else:
        print("Passwort leer lassen, um es bei jeder Anmeldung einzugeben.")
    try:
        return getpass.getpass("Passwort: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("")
        return ""


def _ask_choice(message, choices, default=""):
    print(message)
    for index, choice in enumerate(choices, 1):
        marker = " *" if choice == default else ""
        print("  [%d] %s%s" % (index, choice or "(automatisch)", marker))
    position = choices.index(default) + 1 if default in choices else 1
    answer = _ask("Nummer", str(position))
    try:
        return choices[int(answer) - 1]
    except (ValueError, IndexError):
        return default if default in choices else choices[0]


def print_users(store):
    for note in store.notes:
        print(note)
    if not len(store):
        print("keine Benutzer gespeichert - anlegen mit: python api.py --users")
        return
    print("gespeicherte Benutzer (%s):" % store.path)
    for user in store:
        marker = "*" if user.name.lower() == (store.default or "").lower() else " "
        print(" %s %s" % (marker, user.describe()))
    print("")
    print("* = Standard, wird ohne --user verwendet")


def edit_user(store, user=None):
    """Ask for the fields of one profile and store it."""
    creating = user is None
    user = User() if creating else user
    username = _ask("LPIS-Benutzername (z.B. h12345678)", user.username)
    if not username:
        print("abgebrochen")
        return None
    user["username"] = username
    name = _ask("Bezeichnung", user.name or username)
    if not NAME_PATTERN.match(name or ""):
        print("ungueltige Bezeichnung - erlaubt sind Buchstaben, Ziffern, . _ - und Leerzeichen")
        return None
    user["name"] = name
    password = _ask_password(user.get("password"))
    if password or creating:
        user["password"] = password
    user["msdomain"] = _ask("Microsoft-Domain", user.get("msdomain") or DEFAULT_MSDOMAIN)
    user["mfa_method"] = _ask_choice("2FA-Methode:", list(MFA_METHODS),
                                     user.get("mfa_method", ""))
    store.put(user)
    store.save()
    print("Benutzer %s gespeichert" % user.title)
    return user


def manage(store=None):
    """Small interactive manager: list, add, edit, remove, set default."""
    store = store if store is not None else UserStore()
    if not _has_tty():
        print_users(store)
        return store

    while True:
        print("")
        print_users(store)
        print("")
        leftovers = store.leftover_credentials_files()
        print("  [1] Benutzer hinzufuegen")
        print("  [2] Benutzer bearbeiten")
        print("  [3] Benutzer loeschen")
        print("  [4] Standard-Benutzer waehlen")
        if leftovers:
            print("  [5] alte Zugangsdaten-Dateien loeschen (%d)" % len(leftovers))
        print("  [q] fertig")
        try:
            choice = input("Auswahl: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("")
            return store
        store.notes = []

        if choice == "1":
            edit_user(store)
        elif choice == "2" and len(store):
            name = _ask_choice("Welchen Benutzer bearbeiten?", store.names(), store.default)
            edit_user(store, store.get(name))
        elif choice == "3" and len(store):
            name = _ask_choice("Welchen Benutzer loeschen?", store.names(), store.default)
            if _ask("'%s' wirklich loeschen? (j/n)" % name, "n").lower().startswith("j"):
                store.remove(name)
                store.save()
                print("geloescht")
        elif choice == "4" and len(store):
            name = _ask_choice("Standard-Benutzer:", store.names(), store.default)
            store.set_default(name)
            store.save()
            print("Standard ist jetzt %s" % name)
        elif choice == "5" and leftovers:
            print("uebernommen und nicht mehr noetig:")
            for path in leftovers:
                print("  %s" % path)
            if _ask("wirklich loeschen? (j/n)", "n").lower().startswith("j"):
                removed = store.delete_credentials_files(leftovers)
                print("%d Datei(en) geloescht" % len(removed))
        elif choice.lower() in ("q", "", "5", "6", "fertig", "quit", "exit"):
            return store


if __name__ == "__main__":
    manage()
