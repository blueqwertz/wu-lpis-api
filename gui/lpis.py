"""LPIS client of the desktop app.

This is the app's own layer, not a wrapper around the command line tool: it
navigates LPIS, parses the pages into objects and returns them. Nothing here
prints, asks on a terminal or blocks without a way out - waiting is
interruptible and reports its progress through callbacks.

Shared with the command line tool are only the parts that are libraries rather
than presentation: ``ms_login`` for the Microsoft/Keycloak login chain (it gets
the app's own dialogs handed in), ``users`` for the account database and
``logger`` for the log output.
"""

import datetime
import re
import threading
import time

import mechanize
import ntplib
import requests
from bs4 import BeautifulSoup

from logger import logger
from ms_login import USER_AGENT, LpisSSOSession

START_URL = "https://lpis.wu.ac.at/lpis"
TIMESERVER = "timeserver.wu.ac.at"
NTFY_URL = "https://ntfy.sh/lpis-%s"

# the form of the study selection on the LPIS start page
STUDY_FORM = "ea_stupl"

# forms whose name starts with this are "remove from waiting list" buttons -
# submitting one of those would cancel the registration instead of creating it
WAITLIST_DELETE = "WLDEL"


class LpisError(Exception):
    """Something on the LPIS side did not work out."""


class Cancelled(Exception):
    """The user stopped the running action."""


# ---------------------------------------------------------------------- #
# helpers
# ---------------------------------------------------------------------- #

def _text(node):
    return node.get_text(strip=True) if node else ""


def _float(value):
    value = (value or "").strip()
    if not value or value.upper() == "N/A":
        return None
    try:
        return float(value.replace(",", "."))
    except ValueError:
        return None


def _int(value, default=0):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _capacity(text):
    """"12 / 40" -> (12, 40)"""
    if not text or "/" not in text:
        return None, None
    free, _, total = text.rpartition("/")
    return _int(free.strip(), None), _int(total.strip(), None)


def _check(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled()


# ---------------------------------------------------------------------- #
# what the pages are turned into
# ---------------------------------------------------------------------- #

class Study():
    """One entry of the study dropdown (Studium/Abschnitt)."""

    def __init__(self, name, value):
        self.name = name
        self.value = value


class Course():
    """One Lehrveranstaltung below a plan point."""

    def __init__(self, number):
        self.number = number
        self.semester = ""
        self.professor = ""
        self.name = ""
        self.status = ""
        self.free = None
        self.capacity = None
        self.waitlist = ""
        self.starts = ""
        self.ends = ""
        self.registered_at = ""
        self.form = ""

    @property
    def registered(self):
        return bool(self.registered_at)

    @property
    def full(self):
        return self.free is not None and self.free <= 0

    @property
    def open(self):
        return not self.full and "nicht" not in (self.status or "")

    @property
    def window(self):
        if self.registered_at:
            return "angemeldet %s" % self.registered_at
        if self.starts:
            return "ab %s" % self.starts
        if self.ends:
            return "bis %s" % self.ends
        return ""


class PlanPoint():
    """One Studienplanpunkt, with the courses that belong to it."""

    def __init__(self, identifier):
        self.id = identifier
        self.type = ""
        self.name = ""
        self.depth = 0
        self.status = ""
        self.result = ""
        self.date = ""
        self.attempts = ""
        self.attempts_max = ""
        self.note = ""
        self.courses = []
        self.url = ""

    @property
    def label(self):
        return ("%s %s" % (self.type, self.name)).strip()


class Grade():
    """One row of the Noten page."""

    def __init__(self):
        self.entry_id = ""
        self.exam_type = ""
        self.exam_type_title = ""
        self.title = ""
        self.professor = ""
        self.sst = None
        self.ects = None
        self.grade_text = ""
        self.grade_date = ""
        self.study = ""
        self.study_title = ""
        self.outdated = False
        self.outdated_reason = ""

    @property
    def value(self):
        """The Austrian grade as a number, or None if it does not count."""
        text = (self.grade_text or "").strip().lower()
        if not text or "nicht" in text and "genügend" in text:
            return None
        if "sehr gut" in text:
            return 1.0
        if text == "gut":
            return 2.0
        if "befriedigend" in text:
            return 3.0
        if "genügend" in text:
            return 4.0
        # "mit Erfolg teilgenommen" and friends carry no number
        return None


class Average():
    """An ects weighted average over a set of grades."""

    def __init__(self, label):
        self.label = label
        self.ects = 0.0
        self.weighted = 0.0
        self.items = []

    def add(self, value, ects):
        self.ects += ects
        self.weighted += value * ects
        self.items.append((value, ects))

    @property
    def gpa(self):
        return self.weighted / self.ects if self.ects else None

    def best(self, cap):
        """Average of the best grades up to `cap` ects (Best30 / Best52)."""
        if not self.items:
            return None
        remaining, weighted, total = float(cap), 0.0, 0.0
        for value, ects in sorted(self.items):
            if remaining <= 0:
                break
            take = min(ects, remaining)
            weighted += value * take
            total += take
            remaining -= take
        return weighted / total if total else None


class StudySummary():

    def __init__(self, study, title):
        self.study = study
        self.title = title
        self.total = Average("Gesamt")
        self.semesters = []
        self.years = []


class Registration():
    """What came out of a registration attempt."""

    def __init__(self):
        self.registered = False
        self.message = ""
        self.free = None
        self.waitlist = ""
        self.course = ""


# ---------------------------------------------------------------------- #
# the client
# ---------------------------------------------------------------------- #

class LpisClient():

    def __init__(self, ui=None):
        self.ui = ui
        self.sso = None
        self.browser = None
        self.username = ""
        self.base_url = ""
        self.landing_url = ""

    @property
    def connected(self):
        return self.browser is not None

    # ------------------------------------------------------------------ #
    # login
    # ------------------------------------------------------------------ #

    def login(self, username, password=None, sessionfile=None,
              msdomain="s.wu.ac.at", mfa_method=None):
        self.username = username
        self.sso = LpisSSOSession(
            username=username,
            password=password,
            sessionfile=sessionfile or ("sessions/" + username),
            ms_domain=msdomain or "s.wu.ac.at",
            mfa_method=mfa_method or None,
            ui=self.ui,
        )
        self.browser = self._make_browser(self.sso)
        self._run_login()
        return self.studies()

    def _make_browser(self, sso):
        browser = mechanize.Browser()
        browser.set_handle_robots(False)
        browser.set_handle_refresh(False)
        browser.set_handle_equiv(True)
        browser.set_handle_redirect(True)
        browser.set_handle_referer(True)
        browser.set_debug_http(False)
        browser.set_debug_responses(False)
        # both halves work on the same cookies, so mechanize keeps browsing
        # with the session the sso login established
        browser.set_cookiejar(sso.session.cookies)
        browser.addheaders = [("User-agent", USER_AGENT), ("Accept", "*/*")]
        return browser

    def _run_login(self):
        started = time.time()
        logger.info("logging in %s..." % self.username)
        response = self.sso.login()

        url = response.url
        self.base_url = url[:url.rindex("/") + 1]
        self.landing_url = url

        # hand the page requests already fetched over to mechanize; the
        # transfer headers must not travel with it, the body is decoded
        skip = ("content-encoding", "content-length", "transfer-encoding")
        headers = [(name, value) for name, value in response.headers.items()
                   if name.lower() not in skip]
        self.browser.set_response(mechanize.make_response(
            response.content, headers, url,
            response.status_code, response.reason or "OK"))

        if self.sso.used_stored_session and not self.sso.did_interactive_login:
            logger.info("reused stored session (no password/2FA needed)")
        logger.info("login took %.1fs" % (time.time() - started))

    def relogin(self):
        """Fetch a fresh LPIS session, e.g. before a registration starts."""
        self._require()
        self._run_login()

    def logout(self):
        """Drop the stored session - the next login needs password and 2FA."""
        if self.sso is not None:
            self.sso.clear_session()
            logger.info("stored session removed")
        self.browser = None
        self.sso = None
        self.base_url = ""
        self.landing_url = ""

    # ------------------------------------------------------------------ #
    # navigation
    # ------------------------------------------------------------------ #

    def _require(self):
        if not self.connected:
            raise LpisError("nicht angemeldet")

    def _open(self, url):
        """Open a url below the LPIS session and return its parsed page."""
        self._require()
        response = self.browser.open(url)
        return BeautifulSoup(response.read(), "html.parser")

    def _home(self):
        """Back to the page that carries the study selection form."""
        self._require()
        try:
            self.browser.open(self.landing_url)
            self.browser.select_form(STUDY_FORM)
            return
        except Exception:
            logger.info("LPIS-Seite abgelaufen, neuer Login")
        self._run_login()
        self.browser.select_form(STUDY_FORM)

    def _study_control(self):
        form = self.browser.form
        return form.find_control(form.controls[0].name)

    def studies(self):
        self._home()
        found = []
        for item in self._study_control().get_items():
            if item.attrs.get("id") == "abgewaehlt":
                continue
            labels = item.get_labels()
            found.append(Study(labels[0].text.strip() if labels else item.name,
                               item.name))
        return found

    def _select_study(self, study):
        """Pick the study in the form and submit it, returns the page."""
        self._home()
        control = self._study_control()
        item = control.get(study) if study else control.get(None, None, None, 0)
        item.selected = True
        logger.info("sectionpoint: %s" % item.name)
        return BeautifulSoup(self.browser.submit().read(), "html.parser")

    @staticmethod
    def _table(soup):
        table = soup.find("table", {"class": "b3k-data"})
        if table is None or table.find("tbody") is None:
            raise LpisError("unerwartete LPIS-Seite (keine Datentabelle)")
        return table

    # ------------------------------------------------------------------ #
    # plan points and courses
    # ------------------------------------------------------------------ #

    def plan_points(self, study, progress=None, cancel=None):
        """The whole study plan with the courses of every plan point."""
        soup = self._select_study(study)
        rows = self._table(soup).find("tbody").find_all("tr")

        points = []
        with_courses = [row for row in rows if row.select('a[href*="DLVO"]')]
        done = 0

        for order, row in enumerate(rows, 1):
            _check(cancel)
            point = self._parse_plan_point(row, order)
            if point is None:
                continue
            points.append(point)
            if not point.url:
                continue
            done += 1
            if progress:
                progress(done, len(with_courses), point.name)
            try:
                point.courses, point.note = self._courses(point)
            except LpisError as error:
                logger.warning("%s: %s" % (point.id, error))
        return points

    def _parse_plan_point(self, row, order):
        try:
            second = row.select_one("td:nth-of-type(2)")
            # deliberately not stripped: a plan point without attempts carries
            # a non breaking space there, and that still marks a data row
            if second is None or not second.get_text():
                return None
            anchor = row.a
            if anchor is None or not anchor.get("id"):
                return None

            point = PlanPoint(anchor["id"][1:])
            point.order = order
            first = row.select_one("td:nth-of-type(1)")
            indent = re.findall(r"\d+", first.get("style", "") or "0")
            point.depth = int(int(indent[0]) / 16) if indent else 0
            point.type = _text(first.select_one("span:nth-of-type(1)"))
            point.name = _text(first.select_one("span:nth-of-type(2)"))

            link = row.select_one('a[href*="DLVO"]')
            if link is not None:
                point.url = link["href"]
                point.status = link.get_text(strip=True)

            if "/" in second.get_text():
                point.attempts = _text(second.select_one("span:nth-of-type(1)"))
                point.attempts_max = _text(second.select_one("span:nth-of-type(2)"))

            point.result = _text(row.select_one("td:nth-of-type(3)"))
            point.date = _text(row.select_one("td:nth-of-type(4)"))
            return point
        except Exception as error:
            logger.warning("Studienplanpunkt konnte nicht gelesen werden: %s" % error)
            return None

    def _courses(self, point):
        """The course list of one plan point, plus a note when there is none."""
        soup = self._open(self.base_url + point.url)
        table = soup.find("table", {"class": "b3k-data"})
        if table is None or table.find("tbody") is None:
            return [], point.status
        courses = []
        for row in table.find("tbody").find_all("tr"):
            course = self._parse_course(row)
            if course is not None:
                courses.append(course)
        return courses, "" if courses else point.status

    def _parse_course(self, row):
        try:
            number = _text(row.select_one(".ver_id a"))
            if not number:
                return None
            course = Course(number)
            course.semester = _text(row.select_one(".ver_id span"))
            course.professor = _text(row.select_one(".ver_title div"))

            title = row.find("td", {"class": "ver_title"})
            if title is not None:
                direct = [part.strip() for part in title.find_all(string=True,
                                                                  recursive=False)
                          if part.strip()]
                course.name = direct[0] if direct else ""

            course.status = _text(row.select_one("td.box div"))
            course.free, course.capacity = _capacity(
                _text(row.select_one('div[class*="capacity_entry"]')))

            form = row.select_one("td.action form")
            if form is not None:
                course.form = form.get("name", "")

            stamp = _text(row.select_one("td.action .timestamp span"))
            if stamp.startswith("ab "):
                course.starts = stamp[3:].strip()
            elif stamp.startswith("bis "):
                course.ends = stamp[4:].strip()

            if row.select_one("td.box.active"):
                course.registered_at = _text(
                    row.select_one("td.box.active .timestamp span"))

            waitlist = row.select_one('td.capacity div[title*="Anzahl Warteliste"]')
            if waitlist is not None:
                course.waitlist = waitlist.get_text(strip=True)
            return course
        except Exception as error:
            logger.warning("Lehrveranstaltung konnte nicht gelesen werden: %s" % error)
            return None

    # ------------------------------------------------------------------ #
    # grades
    # ------------------------------------------------------------------ #

    def grades(self):
        soup = self._open(self.base_url + "NT")
        table = self._table(soup)
        found = []
        for row in table.tbody.find_all("tr", recursive=False):
            grade = self._parse_grade(row)
            if grade is not None:
                found.append(grade)
        return found

    def _parse_grade(self, row):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 4:
            return None
        try:
            grade = Grade()
            classes = row.get("class", [])
            grade.outdated = "outdated" in classes
            grade.outdated_reason = row.get("title", "") if grade.outdated else ""

            title_cell = cells[0]
            anchor = title_cell.find("a")
            grade.entry_id = anchor.get("id", "") if anchor else ""

            bold = title_cell.find("b")
            type_span = bold.find("span") if bold else None
            grade.exam_type = _text(type_span)
            grade.exam_type_title = type_span.get("title", "") if type_span else ""

            spans = title_cell.find_all("span", recursive=False)
            grade.title = _text(spans[-1]) if spans else ""

            for line_break in title_cell.find_all("br"):
                following = line_break.next_sibling
                text = following if isinstance(following, str) else ""
                if text and text.strip():
                    grade.professor = re.sub(r"\s+", " ", text.strip())
                    break

            parts = cells[1].find_all("div", recursive=False)
            grade.sst = _float(_text(parts[0])) if len(parts) > 0 else None
            grade.ects = _float(_text(parts[1])) if len(parts) > 1 else None

            grade_spans = cells[2].find_all("span", recursive=False)
            grade.grade_text = _text(grade_spans[0]) if len(grade_spans) > 0 else ""
            grade.grade_date = _text(grade_spans[1]) if len(grade_spans) > 1 else ""

            grade.study = _text(cells[3])
            grade.study_title = cells[3].get("title", "")
            return grade
        except Exception as error:
            logger.warning("Note konnte nicht gelesen werden: %s" % error)
            return None

    @staticmethod
    def summaries(grades):
        """ECTS weighted averages per study, semester and academic year."""
        by_study = {}
        for grade in grades:
            summary = by_study.get(grade.study)
            if summary is None:
                summary = by_study[grade.study] = StudySummary(
                    grade.study, grade.study_title)
            elif grade.study_title:
                summary.title = grade.study_title

            value, ects = grade.value, grade.ects
            if value is None or not ects or ects <= 0:
                continue
            date = _parse_date(grade.grade_date)
            summary.total.add(value, ects)
            _bucket(summary.semesters, _semester(date)).add(value, ects)
            _bucket(summary.years, _academic_year(date)).add(value, ects)

        result = []
        for study in sorted(by_study):
            summary = by_study[study]
            summary.semesters = sorted(
                (entry for entry in summary.semesters if entry.label),
                key=lambda entry: _semester_order(entry.label))
            summary.years = sorted(
                (entry for entry in summary.years if entry.label),
                key=lambda entry: entry.label)
            result.append(summary)
        return result

    # ------------------------------------------------------------------ #
    # registration
    # ------------------------------------------------------------------ #

    def registration_url(self, study, plan_point):
        """The course list of one plan point, reached the way LPIS wants it."""
        soup = self._select_study(study)
        anchor = self._table(soup).find("a", id="S" + plan_point)
        if anchor is None:
            raise LpisError("Studienplanpunkt %s nicht gefunden" % plan_point)
        links = anchor.parent.find_all("a", href=True,
                                       title="Lehrveranstaltungsanmeldung")
        if not links:
            raise LpisError("keine Anmeldung fuer Studienplanpunkt %s moeglich"
                            % plan_point)
        return self.base_url + links[0]["href"]

    @staticmethod
    def _row_of(soup, number):
        for anchor in soup.select("table.b3k-data .ver_id a"):
            if anchor.get_text(strip=True) == number:
                return anchor.find_parent("tr")
        return None

    def time_offset(self):
        """Difference to the WU time server, so the request lands on time."""
        try:
            response = ntplib.NTPClient().request(TIMESERVER, version=3)
            logger.info("time difference to %s: %.4fs" % (TIMESERVER, response.offset))
            return response.offset
        except Exception as error:
            logger.warning("Zeitabgleich mit %s fehlgeschlagen: %s" % (TIMESERVER, error))
            return 0.0

    def register(self, study, plan_point, course, fallback=None, offset=0.7,
                 cancel=None, progress=None):
        """Wait for the registration to open and send it off.

        ``cancel`` is a threading.Event - every wait watches it, so stopping
        takes effect immediately instead of at the end of some loop.
        """
        self._require()
        fallback = (fallback or course).strip()
        course = course.strip()
        lead = float(offset) + self.time_offset()
        logger.info("Vorlauf: %.2fs" % lead)

        url = self.registration_url(study, plan_point)
        while True:
            _check(cancel)
            soup = self._open(url)
            row = self._row_of(soup, course)
            if row is None:
                raise LpisError("Lehrveranstaltung %s nicht gefunden" % course)
            if fallback != course and self._row_of(soup, fallback) is None:
                raise LpisError("Ausweich-Lehrveranstaltung %s nicht gefunden" % fallback)

            start = _parse_start(_text(row.select_one(".action .timestamp span")))
            if start is None:
                break

            waiting = start - time.time()
            if waiting > 600:
                # too far out to keep a session alive - come back five minutes
                # before and log in again
                logger.info("Anmeldung startet um %s, warte bis kurz davor"
                            % start_text(start))
                self._wait_until(start - 300, cancel, progress,
                                 "Neuanmeldung in")
                self.relogin()
                url = self.registration_url(study, plan_point)
                continue

            self._wait_until(start - lead, cancel, progress, "Anmeldung startet in")
            break

        return self._submit(url, course, fallback, cancel, progress)

    def _wait_until(self, deadline, cancel, progress, label):
        while True:
            _check(cancel)
            remaining = deadline - time.time()
            if remaining <= 0:
                return
            if progress:
                progress("%s %s" % (label, _duration(remaining)))
            if cancel is not None:
                # returns as soon as the user cancels instead of sleeping on
                if cancel.wait(min(0.1, remaining)):
                    raise Cancelled()
            else:
                time.sleep(min(0.1, remaining))

    def _submit(self, url, course, fallback, cancel, progress):
        attempt = 0
        while True:
            _check(cancel)
            attempt += 1
            started = time.time()
            soup = self._open(url)
            row = self._row_of(soup, course)
            if row is None:
                raise LpisError("Lehrveranstaltung %s nicht mehr gefunden" % course)
            logger.info("Seite geladen in %.2fs (Versuch %d)"
                        % (time.time() - started, attempt))
            if row.select_one("div.box.possible"):
                break
            if progress:
                progress("Anmeldung noch nicht moeglich, Versuch %d" % attempt)

        logger.opt(colors=True).info("<green>Anmeldung ist moeglich</green>")
        first = self._row_of(soup, course)
        second = self._row_of(soup, fallback)
        free_first = _capacity(_text(first.select_one('div[class*="capacity_entry"]')))[0]
        free_second = _capacity(_text(second.select_one('div[class*="capacity_entry"]')))[0] \
            if second is not None else None
        form_first = self._form_name(first)
        form_second = self._form_name(second)
        logger.info("freie Plaetze: %s / %s" % (free_first, free_second))

        chosen = form_first if (free_first or 0) > 0 else form_second
        if not chosen:
            raise LpisError("kein Anmeldeformular auf der Seite")
        if chosen.startswith(WAITLIST_DELETE):
            raise LpisError("das Formular wuerde die Anmeldung zuruecknehmen "
                            "(%s) - abgebrochen" % chosen)

        logger.info("sende Anmeldung (%s)" % chosen)
        self.browser.select_form(chosen)
        answer = BeautifulSoup(self.browser.submit().read(), "html.parser")
        result = self._result(answer, course)

        # the first course was full and the answer put us on a waiting list -
        # try the fallback as well, exactly like the command line tool does
        if (result.message and "Warteliste" in result.message
                and form_second and form_second != chosen
                and not form_second.startswith(WAITLIST_DELETE)):
            logger.info("zusaetzlich Ausweich-LV versuchen (%s)" % form_second)
            try:
                self.browser.select_form(form_second)
                answer = BeautifulSoup(self.browser.submit().read(), "html.parser")
                fallback_result = self._result(answer, fallback)
                if fallback_result.registered:
                    result = fallback_result
            except Exception as error:
                logger.warning("Ausweich-LV fehlgeschlagen: %s" % error)

        self._notify(result)
        return result

    @staticmethod
    def _form_name(row):
        if row is None:
            return ""
        form = row.select_one("td.action form")
        return form.get("name", "") if form is not None else ""

    def _result(self, soup, course):
        result = Registration()
        result.course = course
        alert = soup.find("div", {"class": "b3k_alert_content"})
        if alert is not None:
            result.message = alert.get_text(strip=True)
            # LPIS answers failures with a sentence containing "nicht"; a
            # waiting list entry is a success of its own kind
            result.registered = ("nicht" not in result.message
                                 or "Warteliste" in result.message)
        row = self._row_of(soup, course)
        if row is not None:
            result.free = _capacity(
                _text(row.select_one('div[class*="capacity_entry"]')))[0]
            waitlist = row.select_one('td.capacity div[title*="Warteliste"] span')
            result.waitlist = _text(waitlist)
            if row.select_one("td.box.active"):
                result.registered = True
        if result.message:
            logger.opt(colors=True).info("<bold>%s</bold>"
                                         % result.message.replace("<", "\\<"))
        return result

    def _notify(self, result):
        """Push the outcome to ntfy, same topics as the command line tool."""
        if not result.message:
            return
        for topic, body in ((self.username, result.message),
                            ("bot", "[%s]: %s" % (self.username, result.message))):
            try:
                requests.post(NTFY_URL % topic, data=body.encode("utf-8"), timeout=5)
            except Exception:
                pass


# ---------------------------------------------------------------------- #
# dates
# ---------------------------------------------------------------------- #

def _parse_date(text):
    if not text:
        return None
    if text.strip() == "vorläufig":
        return datetime.date.today()
    try:
        return datetime.datetime.strptime(text.strip(), "%d.%m.%Y").date()
    except ValueError:
        return None


def _parse_start(text):
    """"ab 01.09.2026 09:00" -> unix time, or None when it is already open."""
    if not text or not text.startswith("ab "):
        return None
    try:
        moment = datetime.datetime.strptime(text[3:].strip(), "%d.%m.%Y %H:%M")
    except ValueError:
        return None
    return time.mktime(moment.timetuple())


def start_text(moment):
    return time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(moment))


def _duration(seconds):
    hours, rest = divmod(max(0.0, seconds), 3600)
    minutes, seconds = divmod(rest, 60)
    return "%02d:%02d:%04.1f" % (int(hours), int(minutes), seconds)


def _semester(date):
    """WS starts on 1.10., SS on 1.3."""
    if date is None:
        return ""
    if date.month >= 10:
        return "WS %d" % date.year
    if date.month <= 2:
        return "WS %d" % (date.year - 1)
    return "SS %d" % date.year


def _semester_order(label):
    """Sort by the month a semester starts: SS in March, WS in October."""
    term, _, year = label.partition(" ")
    return (_int(year, 0), 3 if term == "SS" else 10)


def _academic_year(date):
    if date is None:
        return ""
    start = date.year if date.month >= 10 else date.year - 1
    return "%d/%s" % (start, str(start + 1)[-2:])


def _bucket(entries, label):
    for entry in entries:
        if entry.label == label:
            return entry
    entry = Average(label)
    entries.append(entry)
    return entry
