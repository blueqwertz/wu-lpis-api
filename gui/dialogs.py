"""The small windows: 2FA prompts and the user management."""

import os
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import users as userstore

PAD = {"padx": 8, "pady": 4}


class Modal(tk.Toplevel):
    """A window the rest of the program waits for."""

    def __init__(self, parent, title):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.result = None
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda event: self.cancel())

    def show(self):
        self.update_idletasks()
        self.grab_set()
        self.wait_window(self)
        return self.result

    def cancel(self):
        self.result = None
        self.destroy()


def ask_text(parent, message, secret=False):
    return simpledialog.askstring("Anmeldung", message, parent=parent,
                                  show="*" if secret else "")


def ask_choice(parent, message, choices):
    """Let the user pick one entry, returns the entry or None."""
    if not choices:
        return None

    dialog = Modal(parent, "Auswahl")
    ttk.Label(dialog, text=message, wraplength=380).grid(
        row=0, column=0, columnspan=2, sticky="w", **PAD)
    variable = tk.StringVar(value=choices[0])
    box = ttk.Combobox(dialog, textvariable=variable, values=list(choices),
                       state="readonly", width=44)
    box.grid(row=1, column=0, columnspan=2, sticky="ew", **PAD)

    def accept():
        dialog.result = variable.get()
        dialog.destroy()

    ttk.Button(dialog, text="Abbrechen", command=dialog.cancel).grid(
        row=2, column=0, sticky="e", **PAD)
    ttk.Button(dialog, text="OK", command=accept, default="active").grid(
        row=2, column=1, sticky="w", **PAD)
    box.focus_set()
    dialog.bind("<Return>", lambda event: accept())
    return dialog.show()


class MfaNumber(tk.Toplevel):
    """Shows the number matching value while the login waits for the phone."""

    def __init__(self, parent, number):
        super().__init__(parent)
        self.title("2FA bestaetigen")
        self.resizable(False, False)
        self.transient(parent)
        ttk.Label(self, text="Diese Zahl in der Authenticator-App eingeben:").pack(
            padx=24, pady=(20, 6))
        ttk.Label(self, text=str(number), font=("TkDefaultFont", 42, "bold")).pack(
            padx=24, pady=2)
        ttk.Label(self, text="Das Fenster schliesst sich nach der Bestaetigung.").pack(
            padx=24, pady=(6, 20))
        self.update_idletasks()
        self.geometry("+%d+%d" % (
            parent.winfo_rootx() + max(0, (parent.winfo_width() - self.winfo_width()) // 2),
            parent.winfo_rooty() + 120))


class UserEditor(Modal):
    """Create or change one user profile."""

    def __init__(self, parent, store, user=None):
        super().__init__(parent, "Benutzer bearbeiten" if user else "Benutzer hinzufuegen")
        self.store = store
        self.user = user
        self.creating = user is None
        user = user if user is not None else userstore.User()

        self.name = tk.StringVar(value=user.get("name") or "")
        self.username = tk.StringVar(value=user.get("username") or "")
        self.password = tk.StringVar(value=user.get("password") or "")
        self.save_password = tk.BooleanVar(value=bool(user.get("password")))
        self.msdomain = tk.StringVar(value=user.get("msdomain") or userstore.DEFAULT_MSDOMAIN)
        self.mfa_method = tk.StringVar(value=user.get("mfa_method") or "")
        self.sessiondir = tk.StringVar(value=user.get("sessiondir") or "")

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=10, pady=10)
        body.columnconfigure(1, weight=1)

        def row(index, label, widget):
            ttk.Label(body, text=label).grid(row=index, column=0, sticky="w", padx=4, pady=5)
            widget.grid(row=index, column=1, sticky="ew", padx=4, pady=5)
            return widget

        row(0, "Bezeichnung", ttk.Entry(body, textvariable=self.name, width=32))
        row(1, "LPIS-Benutzername", ttk.Entry(body, textvariable=self.username))
        row(2, "Passwort", ttk.Entry(body, textvariable=self.password, show="*"))
        ttk.Checkbutton(body, variable=self.save_password,
                        text="Passwort speichern (Klartext in users.json)").grid(
            row=3, column=1, sticky="w", padx=4)
        row(4, "Microsoft-Domain", ttk.Entry(body, textvariable=self.msdomain))
        row(5, "2FA-Methode", ttk.Combobox(body, textvariable=self.mfa_method,
                                           values=list(userstore.MFA_METHODS),
                                           state="readonly"))

        folder = ttk.Frame(body)
        folder.columnconfigure(0, weight=1)
        ttk.Entry(folder, textvariable=self.sessiondir).grid(row=0, column=0, sticky="ew")
        ttk.Button(folder, text="...", width=3, command=self._pick_folder).grid(
            row=0, column=1, padx=(4, 0))
        row(6, "Sessions-Ordner", folder)

        ttk.Label(body, text="Leer lassen fuer die Standardwerte. Das Passwort wird "
                             "nur beim ersten Login gebraucht, danach genuegt die "
                             "gespeicherte Session.",
                  wraplength=380, justify="left").grid(
            row=7, column=0, columnspan=2, sticky="w", padx=4, pady=(10, 0))

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(buttons, text="Abbrechen", command=self.cancel).pack(side="right")
        ttk.Button(buttons, text="Speichern", command=self.accept).pack(side="right", padx=6)

    def _pick_folder(self):
        chosen = filedialog.askdirectory(parent=self, title="Ordner fuer Sessions")
        if chosen:
            self.sessiondir.set(chosen)

    def accept(self):
        username = self.username.get().strip()
        if not username:
            messagebox.showwarning("Benutzer", "Bitte einen LPIS-Benutzernamen eingeben.",
                                   parent=self)
            return
        name = self.name.get().strip() or username
        if not userstore.NAME_PATTERN.match(name):
            messagebox.showwarning(
                "Benutzer",
                "Ungueltige Bezeichnung. Erlaubt sind Buchstaben, Ziffern, "
                "Punkt, Unterstrich, Bindestrich und Leerzeichen.", parent=self)
            return
        clash = self.store.get(name) or self.store.get(username)
        if clash is not None and clash is not self.user:
            if not messagebox.askyesno(
                    "Benutzer", "'%s' gibt es schon. Ueberschreiben?" % clash.title,
                    parent=self):
                return

        user = self.user if self.user is not None else userstore.User()
        user["name"] = name
        user["username"] = username
        user["password"] = self.password.get() if self.save_password.get() else ""
        user["msdomain"] = self.msdomain.get().strip() or userstore.DEFAULT_MSDOMAIN
        user["mfa_method"] = self.mfa_method.get().strip()
        user["sessiondir"] = self.sessiondir.get().strip()
        try:
            self.store.put(user)
            self.store.save()
        except (ValueError, OSError) as error:
            messagebox.showerror("Benutzer", "Konnte nicht gespeichert werden:\n%s" % error,
                                 parent=self)
            return
        self.result = user
        self.destroy()


class UserManager(Modal):
    """Add, change, remove profiles and pick the default."""

    def __init__(self, parent, store):
        super().__init__(parent, "Benutzer verwalten")
        self.resizable(True, True)
        self.store = store
        self.minsize(460, 320)

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=10, pady=10)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        self.listbox = tk.Listbox(body, activestyle="dotbox", exportselection=False)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        self.listbox.bind("<Double-Button-1>", lambda event: self.edit())
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.listbox.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=scroll.set)

        buttons = ttk.Frame(body)
        buttons.grid(row=0, column=2, sticky="n", padx=(10, 0))
        for text, command in (("Neu ...", self.create),
                              ("Bearbeiten ...", self.edit),
                              ("Loeschen", self.delete),
                              ("Als Standard", self.make_default)):
            ttk.Button(buttons, text=text, width=16, command=command).pack(pady=3)
        self.cleanup_button = ttk.Button(buttons, text="Altdateien ...", width=16,
                                         command=self.cleanup)
        self.cleanup_button.pack(pady=(16, 3))

        self.hint = ttk.Label(self, text="", wraplength=520, justify="left")
        self.hint.pack(fill="x", padx=14, pady=(0, 6))
        ttk.Button(self, text="Schliessen", command=self.close).pack(
            anchor="e", padx=14, pady=(0, 12))
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()

    def close(self):
        self.result = True
        self.destroy()

    def refresh(self):
        selection = self.listbox.curselection()
        self.listbox.delete(0, "end")
        for user in self.store:
            default = user.name.lower() == (self.store.default or "").lower()
            self.listbox.insert("end", "%s%s%s" % (
                "* " if default else "   ", user.title,
                "  -  Passwort gespeichert" if user.get("password") else ""))
        if selection and selection[0] < self.listbox.size():
            self.listbox.selection_set(selection[0])
        elif self.listbox.size():
            self.listbox.selection_set(0)

        leftovers = self.store.leftover_credentials_files()
        self.cleanup_button.state(["!disabled"] if leftovers else ["disabled"])
        note = "Datenbank: %s (kann auch von Hand bearbeitet werden)" % self.store.path
        if leftovers:
            note += "\n%d alte .credentials-Datei(en) wurden uebernommen und " \
                    "werden nicht mehr gelesen." % len(leftovers)
        self.hint.configure(text=note)

    def selected(self):
        selection = self.listbox.curselection()
        if not selection:
            return None
        return self.store.users[selection[0]]

    def _editor(self, user=None):
        saved = UserEditor(self, self.store, user).show()
        # the editor took the grab away, so take it back
        self.grab_set()
        if saved:
            self.refresh()

    def create(self):
        self._editor()

    def edit(self):
        user = self.selected()
        if user is not None:
            self._editor(user)

    def delete(self):
        user = self.selected()
        if user is None:
            return
        if not messagebox.askyesno("Benutzer", "'%s' loeschen?" % user.title, parent=self):
            return
        self.store.remove(user.name)
        self.store.save()
        self.refresh()

    def make_default(self):
        user = self.selected()
        if user is None:
            return
        self.store.set_default(user.name)
        self.store.save()
        self.refresh()

    def cleanup(self):
        leftovers = self.store.leftover_credentials_files()
        if not leftovers:
            return
        listing = "\n".join(os.path.basename(path) for path in leftovers)
        if not messagebox.askyesno(
                "Alte Zugangsdaten",
                "Diese Dateien wurden in die Benutzer-Datenbank uebernommen und "
                "werden nicht mehr gelesen:\n\n%s\n\nJetzt loeschen?" % listing,
                parent=self):
            return
        removed = self.store.delete_credentials_files(leftovers)
        messagebox.showinfo("Alte Zugangsdaten", "%d Datei(en) geloescht." % len(removed),
                            parent=self)
        self.refresh()
