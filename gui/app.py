"""The main window.

Four tabs, one worker thread, no styling: everything uses the ttk widgets of
the platform, so it looks like a normal Windows or macOS program.
"""

import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import users as userstore

from gui import dialogs, runtime
from gui.worker import LpisSession, Worker

PAD = {"padx": 6, "pady": 4}

MAX_LOG_LINES = 4000


class LpisGui(tk.Tk):

    def __init__(self, bridge):
        super().__init__()
        self.bridge = bridge
        self.store = userstore.UserStore()
        self.session = LpisSession(bridge)
        self.worker = Worker(bridge)
        self.worker.start()

        self.busy = False
        self.pumping = False
        self.current_job = None
        self.mfa_window = None
        self.sectionpoints = []
        self.courses = {}
        self.tree_meta = {}
        self.grade_capture = None
        self.log_views = []

        self.title("WU LPIS %s" % runtime.version())
        self.geometry("980x700")
        self.minsize(820, 560)
        self.protocol("WM_DELETE_WINDOW", self._quit)

        self._build_account()
        self._build_tabs()
        self._build_statusbar()

        self._refresh_users()
        self._update_buttons()
        for note in self.store.notes:
            self._log(note)
        self.after(80, self._pump)
        self.after(1500, self._check_version)

    # ------------------------------------------------------------------ #
    # layout
    # ------------------------------------------------------------------ #

    def _build_account(self):
        frame = ttk.LabelFrame(self, text="Zugang")
        frame.pack(fill="x", padx=10, pady=(10, 6))
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(4, weight=1)

        ttk.Label(frame, text="Benutzer").grid(row=0, column=0, sticky="w", **PAD)
        self.user_variable = tk.StringVar()
        self.user_box = ttk.Combobox(frame, textvariable=self.user_variable,
                                     state="readonly", width=28)
        self.user_box.grid(row=0, column=1, sticky="ew", **PAD)
        self.user_box.bind("<<ComboboxSelected>>", lambda event: self._user_changed())
        self.manage_button = ttk.Button(frame, text="Verwalten ...",
                                        command=self._manage_users)
        self.manage_button.grid(row=0, column=2, sticky="w", **PAD)

        ttk.Label(frame, text="Passwort").grid(row=0, column=3, sticky="e", **PAD)
        self.password_variable = tk.StringVar()
        self.password_entry = ttk.Entry(frame, textvariable=self.password_variable, show="*")
        self.password_entry.grid(row=0, column=4, sticky="ew", **PAD)
        self.password_entry.bind("<Return>", lambda event: self._login())

        self.login_button = ttk.Button(frame, text="Anmelden", command=self._login)
        self.login_button.grid(row=0, column=5, sticky="e", **PAD)
        self.logout_button = ttk.Button(frame, text="Abmelden", command=self._logout)
        self.logout_button.grid(row=0, column=6, sticky="e", **PAD)

        self.account_hint = ttk.Label(
            frame, text="Das Passwort wird nur beim ersten Login gebraucht - "
                        "danach genuegt die gespeicherte Session.")
        self.account_hint.grid(row=1, column=0, columnspan=7, sticky="w", padx=8, pady=(0, 6))

    def _build_tabs(self):
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=10)
        self._build_courses_tab()
        self._build_registration_tab()
        self._build_grades_tab()
        self._build_log_tab()

    def _build_courses_tab(self):
        tab = ttk.Frame(self.tabs)
        self.tabs.add(tab, text="Lehrveranstaltungen")

        top = ttk.Frame(tab)
        top.pack(fill="x", pady=8)
        ttk.Label(top, text="Studium").pack(side="left", padx=(4, 6))
        self.course_study = tk.StringVar()
        self.course_study_box = ttk.Combobox(top, textvariable=self.course_study,
                                             state="readonly", width=48)
        self.course_study_box.pack(side="left")
        self.load_courses_button = ttk.Button(top, text="Laden", command=self._load_courses)
        self.load_courses_button.pack(side="left", padx=6)
        self.take_over_button = ttk.Button(top, text="Fuer Auswahl anmelden ...",
                                           command=self._take_over_selection)
        self.take_over_button.pack(side="left")

        columns = ("id", "semester", "prof", "frei", "status", "zeitraum")
        self.course_tree = ttk.Treeview(tab, columns=columns, show="tree headings")
        self.course_tree.heading("#0", text="Studienplanpunkt / Lehrveranstaltung")
        self.course_tree.column("#0", width=340, minwidth=200)
        for name, title, width in (("id", "LV-Nr.", 70), ("semester", "Semester", 90),
                                   ("prof", "Vortragende", 170), ("frei", "Frei", 70),
                                   ("status", "Status", 170), ("zeitraum", "Anmeldung", 190)):
            self.course_tree.heading(name, text=title)
            self.course_tree.column(name, width=width, minwidth=50,
                                    anchor="e" if name == "frei" else "w")
        self.course_tree.tag_configure("voll", foreground="#8a8a8a")
        self.course_tree.bind("<Double-Button-1>",
                              lambda event: self._take_over_selection())

        scroll = ttk.Scrollbar(tab, orient="vertical", command=self.course_tree.yview)
        self.course_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.course_tree.pack(fill="both", expand=True, pady=(0, 8))

    def _build_registration_tab(self):
        tab = ttk.Frame(self.tabs)
        self.tabs.add(tab, text="Anmeldung")

        form = ttk.LabelFrame(tab, text="Anmeldung vorbereiten")
        form.pack(fill="x", pady=8)
        form.columnconfigure(1, weight=1)

        self.reg_study = tk.StringVar()
        self.reg_planobject = tk.StringVar()
        self.reg_course = tk.StringVar()
        self.reg_course2 = tk.StringVar()
        self.reg_offset = tk.StringVar(value="0.7")

        self.reg_study_box = ttk.Combobox(form, textvariable=self.reg_study,
                                          state="readonly")
        rows = (
            ("Studium", self.reg_study_box),
            ("Studienplanpunkt", ttk.Entry(form, textvariable=self.reg_planobject)),
            ("LV-Nummer", ttk.Entry(form, textvariable=self.reg_course)),
            ("Ausweich-LV (optional)", ttk.Entry(form, textvariable=self.reg_course2)),
            ("Vorlauf in Sekunden", ttk.Entry(form, textvariable=self.reg_offset, width=10)),
        )
        for index, (label, widget) in enumerate(rows):
            ttk.Label(form, text=label).grid(row=index, column=0, sticky="w", **PAD)
            widget.grid(row=index, column=1, sticky="ew" if index < 4 else "w", **PAD)

        buttons = ttk.Frame(form)
        buttons.grid(row=len(rows), column=0, columnspan=2, sticky="w", padx=6, pady=8)
        self.start_button = ttk.Button(buttons, text="Anmeldung starten",
                                       command=self._start_registration)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="Abbrechen",
                                      command=self._stop_registration)
        self.stop_button.pack(side="left", padx=6)

        ttk.Label(form, wraplength=760, justify="left",
                  text="Die Werte lassen sich im Tab \"Lehrveranstaltungen\" per "
                       "Doppelklick uebernehmen. Das Programm wartet bis zum "
                       "Anmeldebeginn und schickt die Anmeldung dann automatisch ab - "
                       "das Fenster dabei offen lassen.").grid(
            row=len(rows) + 1, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 8))

        output = ttk.LabelFrame(tab, text="Verlauf")
        output.pack(fill="both", expand=True, pady=(0, 8))
        self.log_views.append(self._make_log_view(output))

    def _build_grades_tab(self):
        tab = ttk.Frame(self.tabs)
        self.tabs.add(tab, text="Noten")

        top = ttk.Frame(tab)
        top.pack(fill="x", pady=8)
        self.load_grades_button = ttk.Button(top, text="Noten laden",
                                             command=self._load_grades)
        self.load_grades_button.pack(side="left", padx=4)

        panes = ttk.PanedWindow(tab, orient="vertical")
        panes.pack(fill="both", expand=True, pady=(0, 8))

        holder = ttk.Frame(panes)
        columns = ("typ", "titel", "prof", "ects", "note", "datum", "studium")
        self.grade_tree = ttk.Treeview(holder, columns=columns, show="headings")
        for name, title, width in (("typ", "Typ", 60), ("titel", "Titel", 300),
                                   ("prof", "Vortragende", 170), ("ects", "ECTS", 60),
                                   ("note", "Note", 150), ("datum", "Datum", 90),
                                   ("studium", "Studium", 110)):
            self.grade_tree.heading(name, text=title)
            self.grade_tree.column(name, width=width, minwidth=50,
                                   anchor="e" if name == "ects" else "w")
        scroll = ttk.Scrollbar(holder, orient="vertical", command=self.grade_tree.yview)
        self.grade_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.grade_tree.pack(fill="both", expand=True)
        panes.add(holder, weight=3)

        summary = ttk.LabelFrame(panes, text="Durchschnitt")
        self.grade_summary = tk.Text(summary, height=8, wrap="none", state="disabled")
        summary_scroll = ttk.Scrollbar(summary, orient="vertical",
                                       command=self.grade_summary.yview)
        self.grade_summary.configure(yscrollcommand=summary_scroll.set)
        summary_scroll.pack(side="right", fill="y")
        self.grade_summary.pack(fill="both", expand=True)
        panes.add(summary, weight=1)

    def _build_log_tab(self):
        tab = ttk.Frame(self.tabs)
        self.tabs.add(tab, text="Protokoll")
        self.log_tab = tab

        buttons = ttk.Frame(tab)
        buttons.pack(fill="x", pady=8)
        ttk.Button(buttons, text="Leeren", command=self._clear_log).pack(side="left", padx=4)
        ttk.Button(buttons, text="Speichern ...", command=self._save_log).pack(side="left")

        self.log_views.append(self._make_log_view(tab))

    def _make_log_view(self, parent):
        holder = ttk.Frame(parent)
        holder.pack(fill="both", expand=True, pady=(0, 8))
        text = tk.Text(holder, wrap="none", state="disabled", height=10,
                       font=("TkFixedFont",))
        vertical = ttk.Scrollbar(holder, orient="vertical", command=text.yview)
        horizontal = ttk.Scrollbar(holder, orient="horizontal", command=text.xview)
        text.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        vertical.pack(side="right", fill="y")
        horizontal.pack(side="bottom", fill="x")
        text.pack(fill="both", expand=True)
        return text

    def _build_statusbar(self):
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=10, pady=(4, 10))
        self.status = ttk.Label(bar, text="nicht angemeldet", anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=140)
        self.progress.pack(side="right")

    # ------------------------------------------------------------------ #
    # users
    # ------------------------------------------------------------------ #

    def _refresh_users(self, select=None):
        titles = self.store.titles()
        self.user_box.configure(values=titles)
        wanted = select or self.user_variable.get()
        user = self.store.get(wanted) or self.store.default_user()
        if user is None and len(self.store):
            user = self.store.users[0]
        self.user_variable.set(user.title if user else "")
        self._user_changed()

    def _current_user(self):
        return self.store.get(self.user_variable.get())

    def _user_changed(self):
        user = self._current_user()
        self.password_variable.set(user.get("password") if user else "")
        if user:
            self.reg_planobject.set(user.get("planobject") or "")
            self.reg_course.set(user.get("course") or "")
            self.reg_course2.set(user.get("course2") or "")
            self.reg_offset.set(str(user.get("offset") or 0.7))
        self._update_buttons()

    def _manage_users(self):
        dialogs.UserManager(self, self.store).show()
        self._refresh_users()

    # ------------------------------------------------------------------ #
    # actions
    # ------------------------------------------------------------------ #

    def _login(self):
        if self.busy:
            return
        user = self._current_user()
        if user is None:
            if messagebox.askyesno("Anmelden", "Es ist kein Benutzer angelegt. "
                                               "Jetzt einen anlegen?"):
                if dialogs.UserEditor(self, self.store).show():
                    self._refresh_users()
            return
        password = self.password_variable.get()
        # a copy, so editing the profile while the login runs cannot change
        # the settings under the worker's feet
        payload = userstore.User(**dict(user))
        self._set_status("Anmeldung laeuft ...")
        self.worker.submit("login", lambda: self.session.login(payload, password))

    def _logout(self):
        if self.busy or not self.session.connected:
            return
        if not messagebox.askyesno(
                "Abmelden", "Gespeicherte Session loeschen? Die naechste Anmeldung "
                            "braucht wieder Passwort und 2FA."):
            return
        self.worker.submit("logout", self.session.logout)

    def _load_courses(self):
        if self.busy or not self.session.connected:
            return
        self._set_status("Lehrveranstaltungen werden geladen ...")
        value = self._selected_sectionpoint(self.course_study.get())
        self.worker.submit("infos", lambda: self.session.infos(value))

    def _load_grades(self):
        if self.busy or not self.session.connected:
            return
        self._set_status("Noten werden geladen ...")
        self.grade_capture = []
        self.worker.submit("grades", self.session.grades)

    def _start_registration(self):
        if self.busy or not self.session.connected:
            return
        planobject = self.reg_planobject.get().strip()
        course = self.reg_course.get().strip()
        if not planobject or not course:
            messagebox.showwarning("Anmeldung",
                                   "Studienplanpunkt und LV-Nummer werden gebraucht.")
            return
        try:
            offset = float(self.reg_offset.get().replace(",", "."))
        except ValueError:
            messagebox.showwarning("Anmeldung", "Vorlauf muss eine Zahl sein.")
            return

        sectionpoint = self._selected_sectionpoint(self.reg_study.get())
        course2 = self.reg_course2.get().strip()
        self._remember_registration(sectionpoint, planobject, course, course2, offset)
        self._set_status("Anmeldung laeuft - Fenster offen lassen ...")
        self.worker.submit("registration", lambda: self.session.registration(
            sectionpoint, planobject, course, course2, offset))

    def _stop_registration(self):
        if not self.busy:
            return
        if messagebox.askyesno("Abbrechen", "Laufende Anmeldung abbrechen?"):
            self.worker.stop_current()

    def _remember_registration(self, sectionpoint, planobject, course, course2, offset):
        user = self._current_user()
        if user is None:
            return
        user["sectionpoint"] = sectionpoint or ""
        user["planobject"] = planobject
        user["course"] = course
        user["course2"] = course2
        user["offset"] = offset
        try:
            self.store.save()
        except OSError as error:
            self._log("Einstellungen konnten nicht gespeichert werden: %s" % error)

    # ------------------------------------------------------------------ #
    # course tree
    # ------------------------------------------------------------------ #

    def _selected_sectionpoint(self, title):
        for entry in self.sectionpoints:
            if entry["name"] == title:
                return entry["value"]
        return self.sectionpoints[0]["value"] if self.sectionpoints else None

    def _fill_sectionpoints(self, entries):
        self.sectionpoints = entries or []
        names = [entry["name"] for entry in self.sectionpoints]
        self.course_study_box.configure(values=names)
        self.reg_study_box.configure(values=names)

        user = self._current_user()
        stored = (user.get("sectionpoint") if user else "") or ""
        chosen = names[0] if names else ""
        for entry in self.sectionpoints:
            if entry["value"] == stored:
                chosen = entry["name"]
                break
        self.course_study.set(chosen)
        self.reg_study.set(chosen)

    def _fill_courses(self, planpunkte):
        self.courses = planpunkte or {}
        self.tree_meta = {}
        self.course_tree.delete(*self.course_tree.get_children())

        stack = []  # (depth, tree item)
        for key, planpunkt in self.courses.items():
            depth = int(planpunkt.get("depth") or 0)
            while stack and stack[-1][0] >= depth:
                stack.pop()
            parent = stack[-1][1] if stack else ""
            label = "%s %s" % (planpunkt.get("type", ""), planpunkt.get("name", ""))
            item = self.course_tree.insert(
                parent, "end", text=label.strip(),
                values=(key, "", "", "", planpunkt.get("lv_status", ""),
                        planpunkt.get("result", "")), open=depth < 2)
            self.tree_meta[item] = {"pp": key}
            stack.append((depth, item))

            lvs = planpunkt.get("lvs") or {}
            if "" in lvs:
                self.course_tree.insert(item, "end", text=planpunkt.get("lv_status", ""))
                continue
            for number, lv in lvs.items():
                free = lv.get("free", "")
                window = ""
                if lv.get("date_start"):
                    window = "ab %s" % lv["date_start"]
                elif lv.get("date_end"):
                    window = "bis %s" % lv["date_end"]
                if lv.get("registerd_at"):
                    window = "angemeldet %s" % lv["registerd_at"]
                full = str(free) in ("0", "") or "nicht" in (lv.get("status") or "")
                child = self.course_tree.insert(
                    item, "end", text=lv.get("name", ""),
                    values=(number, lv.get("semester", ""), lv.get("prof", ""),
                            "%s/%s" % (free, lv.get("capacity", "")),
                            lv.get("status", ""), window),
                    tags=("voll",) if full else ())
                self.tree_meta[child] = {"pp": key, "lv": number}

    def _take_over_selection(self):
        selection = self.course_tree.selection()
        meta = self.tree_meta.get(selection[0]) if selection else None
        if not meta or not meta.get("lv"):
            messagebox.showinfo("Anmeldung",
                                "Bitte eine Lehrveranstaltung (Zeile mit LV-Nummer) waehlen.")
            return
        self.reg_planobject.set(meta["pp"])
        self.reg_course.set(meta["lv"])
        self.reg_study.set(self.course_study.get())
        self.tabs.select(1)

    def _fill_grades(self, entries):
        self.grade_tree.delete(*self.grade_tree.get_children())
        for entry in entries or []:
            ects = entry.get("ects")
            self.grade_tree.insert(
                "", "end",
                values=(entry.get("exam_type", ""), entry.get("title", ""),
                        entry.get("professor", ""), "" if ects is None else "%g" % ects,
                        entry.get("grade_text", ""), entry.get("grade_date", ""),
                        entry.get("study", "")))

    def _fill_grade_summary(self):
        lines = self.grade_capture or []
        start = next((index for index, line in enumerate(lines)
                      if line.startswith("GPA by Study")), None)
        text = "\n".join(lines[start:]) if start is not None else \
            "keine Durchschnittswerte gefunden"
        self.grade_summary.configure(state="normal")
        self.grade_summary.delete("1.0", "end")
        self.grade_summary.insert("end", text)
        self.grade_summary.configure(state="disabled")
        self.grade_capture = None

    # ------------------------------------------------------------------ #
    # events from the worker
    # ------------------------------------------------------------------ #

    def _pump(self):
        # a modal dialog runs its own event loop, so this timer fires again
        # while the queue is still being worked on. Without the flag every
        # dialog would leave a second timer chain behind
        if self.pumping:
            return
        self.pumping = True
        try:
            while True:
                kind, payload = self.bridge.events.get_nowait()
                self._handle(kind, payload)
        except queue.Empty:
            pass
        finally:
            self.pumping = False
        self.after(80, self._pump)

    def _handle(self, kind, payload):
        if kind == "log":
            self._log(payload["line"])
        elif kind == "status":
            if payload["text"]:
                self._set_status(payload["text"])
        elif kind == "job_start":
            self._set_busy(True, payload["job"])
        elif kind == "job_done":
            self._job_done(payload)
        elif kind == "mfa_number":
            self._show_mfa(payload["number"])
        elif kind == "mfa_number_done":
            self._close_mfa()
        elif kind == "ask_text":
            self.bridge.reply(payload, dialogs.ask_text(self, payload["message"]))
        elif kind == "ask_choice":
            self.bridge.reply(payload, dialogs.ask_choice(
                self, payload["message"], payload["choices"]))

    def _job_done(self, payload):
        job, error, result = payload["job"], payload["error"], payload["result"]
        self._set_busy(False, None)
        self._close_mfa()

        if error:
            self._job_failed(job, error)
            return

        if job == "login":
            self._fill_sectionpoints(result)
            self._set_status("angemeldet als %s" % self.session.username)
        elif job == "logout":
            self._set_status("abgemeldet")
            self.course_tree.delete(*self.course_tree.get_children())
            self.grade_tree.delete(*self.grade_tree.get_children())
        elif job == "infos":
            self._fill_courses(result)
            self._set_status("%d Studienplanpunkte geladen" % len(result or {}))
        elif job == "grades":
            self._fill_grades(result)
            self._fill_grade_summary()
            self._set_status("%d Noten geladen" % len(result or []))
        elif job == "registration":
            self._set_status("Anmeldung beendet - Details im Protokoll")
        self._update_buttons()

    def _job_failed(self, job, error):
        self.grade_capture = None
        if error == "abgebrochen":
            self._set_status("abgebrochen")
            self._update_buttons()
            return
        last = [line for line in error.strip().splitlines() if line.strip()]
        message = last[-1] if last else "unbekannter Fehler"
        self._log(error.rstrip())
        if job == "login":
            self.session.api = None
        self._set_status("Fehler: %s" % message)
        self._update_buttons()
        messagebox.showerror("Fehler", "%s\n\nDetails im Tab \"Protokoll\"." % message)

    # ------------------------------------------------------------------ #
    # small helpers
    # ------------------------------------------------------------------ #

    def _log(self, line):
        if self.grade_capture is not None:
            self.grade_capture.append(line)
        for view in self.log_views:
            view.configure(state="normal")
            view.insert("end", line + "\n")
            if int(view.index("end-1c").split(".")[0]) > MAX_LOG_LINES:
                view.delete("1.0", "%d.0" % (MAX_LOG_LINES // 4))
            view.see("end")
            view.configure(state="disabled")

    def _clear_log(self):
        for view in self.log_views:
            view.configure(state="normal")
            view.delete("1.0", "end")
            view.configure(state="disabled")

    def _save_log(self):
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".log", initialfile="lpis.log",
            filetypes=[("Logdatei", "*.log"), ("Alle Dateien", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(self.log_views[-1].get("1.0", "end"))
        except OSError as error:
            messagebox.showerror("Speichern", str(error))

    def _set_status(self, text):
        self.status.configure(text=text)

    def _set_busy(self, busy, job):
        self.busy = busy
        self.current_job = job
        if busy:
            self.progress.start(60)
        else:
            self.progress.stop()
        self._update_buttons()

    def _update_buttons(self):
        connected = self.session.connected
        idle = not self.busy

        def state(widget, enabled):
            widget.state(["!disabled"] if enabled else ["disabled"])

        state(self.login_button, idle and not connected)
        state(self.logout_button, idle and connected)
        state(self.user_box, idle and not connected)
        state(self.manage_button, idle)
        state(self.password_entry, idle and not connected)
        state(self.load_courses_button, idle and connected)
        state(self.take_over_button, idle and connected)
        state(self.load_grades_button, idle and connected)
        state(self.start_button, idle and connected)
        state(self.stop_button, self.busy and self.current_job == "registration")

    def _show_mfa(self, number):
        self._close_mfa()
        self.mfa_window = dialogs.MfaNumber(self, number)

    def _close_mfa(self):
        if self.mfa_window is not None:
            try:
                self.mfa_window.destroy()
            except tk.TclError:
                pass
            self.mfa_window = None

    def _check_version(self):
        """Say so when a newer version is on github - never update by itself."""
        def check():
            try:
                import updater
                remote = updater.get_remote_version()
                local = runtime.version()
                if remote and updater.version_key(remote) > updater.version_key(local):
                    self.bridge.log("neue Version %s verfuegbar (installiert: %s)"
                                    % (remote, local))
            except Exception:
                pass

        threading.Thread(target=check, daemon=True).start()

    def _quit(self):
        if self.busy and not messagebox.askyesno(
                "Beenden", "Es laeuft noch eine Aktion. Trotzdem beenden?"):
            return
        self.destroy()
