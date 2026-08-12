"""The main window.

Four tabs, one worker thread, no styling: everything uses the ttk widgets of
the platform, so it looks like a normal Windows or macOS program. The LPIS work
itself happens in gui/lpis.py - this file only shows what comes back.
"""

import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import users as userstore

from gui import dialogs, runtime
from gui.bridge import PromptUI
from gui.lpis import LpisClient
from gui.worker import Worker

PAD = {"padx": 6, "pady": 4}

MAX_LOG_LINES = 4000


def _number(value, digits=2):
    return "-" if value is None else ("%%.%df" % digits) % value


def _ects(value):
    return "%g" % (value or 0)


def render_summaries(summaries):
    """The averages as the text shown below the grade list."""
    if not summaries:
        return "keine benoteten Pruefungen gefunden"
    lines = []
    for summary in summaries:
        title = summary.study
        if summary.title:
            title = "%s - %s" % (title, summary.title)
        lines.append(title)
        lines.append("  Gesamt: %s   (%s ECTS)"
                     % (_number(summary.total.gpa), _ects(summary.total.ects)))
        for label, entries, cap in (("Semester", summary.semesters, 30),
                                    ("Studienjahre", summary.years, 52)):
            if not entries:
                continue
            lines.append("  %s:" % label)
            for entry in entries:
                line = "    %-9s %s   (%s ECTS)" % (
                    entry.label, _number(entry.gpa), _ects(entry.ects))
                if entry.ects > cap:
                    line += "   Best%d %s" % (cap, _number(entry.best(cap), 3))
                lines.append(line)
        lines.append("")
    return "\n".join(lines).rstrip()


class LpisGui(tk.Tk):

    def __init__(self, bridge):
        super().__init__()
        self.bridge = bridge
        self.store = userstore.UserStore()
        self.client = LpisClient(PromptUI(bridge))
        self.worker = Worker(bridge)
        self.worker.start()

        self.busy = False
        self.pumping = False
        self.current_job = None
        self.mfa_window = None
        self.studies = []
        self.tree_meta = {}
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

        ttk.Label(frame, text="Das Passwort wird nur beim ersten Login gebraucht - "
                              "danach genuegt die gespeicherte Session.").grid(
            row=1, column=0, columnspan=7, sticky="w", padx=8, pady=(0, 6))

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
        self.grade_tree.tag_configure("ungueltig", foreground="#8a8a8a")
        scroll = ttk.Scrollbar(holder, orient="vertical", command=self.grade_tree.yview)
        self.grade_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.grade_tree.pack(fill="both", expand=True)
        panes.add(holder, weight=3)

        summary = ttk.LabelFrame(panes, text="Durchschnitt (ECTS-gewichtet)")
        self.grade_summary = tk.Text(summary, height=9, wrap="none", state="disabled",
                                     font=("TkFixedFont",))
        summary_scroll = ttk.Scrollbar(summary, orient="vertical",
                                       command=self.grade_summary.yview)
        self.grade_summary.configure(yscrollcommand=summary_scroll.set)
        summary_scroll.pack(side="right", fill="y")
        self.grade_summary.pack(fill="both", expand=True)
        panes.add(summary, weight=1)

    def _build_log_tab(self):
        tab = ttk.Frame(self.tabs)
        self.tabs.add(tab, text="Protokoll")

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
        self.user_box.configure(values=self.store.titles())
        user = self.store.get(select or self.user_variable.get()) or self.store.default_user()
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

        username = user.username
        password = self.password_variable.get()
        directory = (user.get("sessiondir") or "").strip()
        sessionfile = os.path.join(directory, username) if directory \
            else os.path.join("sessions", username)
        msdomain = user.get("msdomain") or "s.wu.ac.at"
        method = (user.get("mfa_method") or "").strip() or None

        def job():
            runtime.log_for_user(username)
            return self.client.login(username, password, sessionfile=sessionfile,
                                     msdomain=msdomain, mfa_method=method)

        self._set_status("Anmeldung laeuft ...")
        self.worker.submit("login", job)

    def _logout(self):
        if self.busy or not self.client.connected:
            return
        if not messagebox.askyesno(
                "Abmelden", "Gespeicherte Session loeschen? Die naechste Anmeldung "
                            "braucht wieder Passwort und 2FA."):
            return
        self.worker.submit("logout", self.client.logout)

    def _load_courses(self):
        if self.busy or not self.client.connected:
            return
        study = self._selected_study(self.course_study.get())

        def progress(done, total, name):
            self.bridge.status("Lade Lehrveranstaltungen %d/%d - %s" % (done, total, name))

        self._set_status("Lehrveranstaltungen werden geladen ...")
        self.worker.submit("courses", lambda: self.client.plan_points(
            study, progress=progress, cancel=self.worker.cancel))

    def _load_grades(self):
        if self.busy or not self.client.connected:
            return

        def job():
            grades = self.client.grades()
            return grades, self.client.summaries(grades)

        self._set_status("Noten werden geladen ...")
        self.worker.submit("grades", job)

    def _start_registration(self):
        if self.busy or not self.client.connected:
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

        study = self._selected_study(self.reg_study.get())
        fallback = self.reg_course2.get().strip()
        self._remember_registration(study, planobject, course, fallback, offset)

        self._set_status("Anmeldung laeuft - Fenster offen lassen ...")
        self.worker.submit("registration", lambda: self.client.register(
            study, planobject, course, fallback, offset,
            cancel=self.worker.cancel, progress=self.bridge.status))

    def _stop_registration(self):
        if self.busy and messagebox.askyesno("Abbrechen", "Laufende Aktion abbrechen?"):
            self.worker.stop_current()

    def _remember_registration(self, study, planobject, course, fallback, offset):
        user = self._current_user()
        if user is None:
            return
        user["sectionpoint"] = study or ""
        user["planobject"] = planobject
        user["course"] = course
        user["course2"] = fallback
        user["offset"] = offset
        try:
            self.store.save()
        except OSError as error:
            self._log("Einstellungen konnten nicht gespeichert werden: %s" % error)

    # ------------------------------------------------------------------ #
    # filling the views
    # ------------------------------------------------------------------ #

    def _selected_study(self, title):
        for study in self.studies:
            if study.name == title:
                return study.value
        return self.studies[0].value if self.studies else None

    def _fill_studies(self, studies):
        self.studies = studies or []
        names = [study.name for study in self.studies]
        self.course_study_box.configure(values=names)
        self.reg_study_box.configure(values=names)

        user = self._current_user()
        stored = (user.get("sectionpoint") if user else "") or ""
        chosen = names[0] if names else ""
        for study in self.studies:
            if study.value == stored:
                chosen = study.name
                break
        self.course_study.set(chosen)
        self.reg_study.set(chosen)

    def _fill_courses(self, points):
        self.tree_meta = {}
        self.course_tree.delete(*self.course_tree.get_children())

        stack = []  # (depth, tree item)
        for point in points or []:
            while stack and stack[-1][0] >= point.depth:
                stack.pop()
            parent = stack[-1][1] if stack else ""
            item = self.course_tree.insert(
                parent, "end", text=point.label,
                values=(point.id, "", "", "", point.status, point.result),
                open=point.depth < 2)
            self.tree_meta[item] = {"pp": point.id}
            stack.append((point.depth, item))

            if point.note:
                self.course_tree.insert(item, "end", text=point.note)
            for course in point.courses:
                free = "" if course.free is None else "%s/%s" % (course.free,
                                                                 course.capacity)
                child = self.course_tree.insert(
                    item, "end", text=course.name,
                    values=(course.number, course.semester, course.professor,
                            free, course.status, course.window),
                    tags=() if course.open else ("voll",))
                self.tree_meta[child] = {"pp": point.id, "lv": course.number}

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

    def _fill_grades(self, grades, summaries):
        self.grade_tree.delete(*self.grade_tree.get_children())
        for grade in grades or []:
            self.grade_tree.insert(
                "", "end",
                values=(grade.exam_type, grade.title, grade.professor,
                        "" if grade.ects is None else "%g" % grade.ects,
                        grade.grade_text, grade.grade_date, grade.study),
                tags=("ungueltig",) if grade.outdated else ())
        self.grade_summary.configure(state="normal")
        self.grade_summary.delete("1.0", "end")
        self.grade_summary.insert("end", render_summaries(summaries))
        self.grade_summary.configure(state="disabled")

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
            self._fill_studies(result)
            self._set_status("angemeldet als %s" % self.client.username)
        elif job == "logout":
            self._set_status("abgemeldet")
            self.studies = []
            self.course_tree.delete(*self.course_tree.get_children())
            self.grade_tree.delete(*self.grade_tree.get_children())
        elif job == "courses":
            self._fill_courses(result)
            courses = sum(len(point.courses) for point in result or [])
            self._set_status("%d Studienplanpunkte, %d Lehrveranstaltungen"
                             % (len(result or []), courses))
        elif job == "grades":
            grades, summaries = result
            self._fill_grades(grades, summaries)
            self._set_status("%d Noten geladen" % len(grades))
        elif job == "registration":
            self._registration_done(result)
        self._update_buttons()

    def _registration_done(self, result):
        if result is None:
            self._set_status("Anmeldung beendet")
            return
        self._set_status(result.message or ("angemeldet" if result.registered
                                            else "Anmeldung beendet"))
        messagebox.showinfo(
            "Anmeldung",
            result.message or ("Anmeldung fuer %s abgeschickt." % result.course))

    def _job_failed(self, job, error):
        if error == "abgebrochen":
            self._set_status("abgebrochen")
            self._update_buttons()
            return
        lines = [line for line in error.strip().splitlines() if line.strip()]
        message = lines[-1] if lines else "unbekannter Fehler"
        if len(lines) > 1:
            self._log(error.rstrip())
        else:
            self._log("Fehler: %s" % message)
        if job == "login":
            self.client.browser = None
        self._set_status("Fehler: %s" % message)
        self._update_buttons()
        messagebox.showerror("Fehler", "%s\n\nDetails im Tab \"Protokoll\"." % message)

    # ------------------------------------------------------------------ #
    # small helpers
    # ------------------------------------------------------------------ #

    def _log(self, line):
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
        connected = self.client.connected
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
        state(self.stop_button, self.busy)

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
