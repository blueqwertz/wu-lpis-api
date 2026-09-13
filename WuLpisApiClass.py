#!/usr/bin/env python
# -*- coding: utf-8 -*-

import datetime, re, os, time, pickle, sys, copy, threading, shutil
from concurrent.futures import ThreadPoolExecutor
from lxml import html
from bs4 import BeautifulSoup
import mechanize, time
import ntplib
from logger import logger
import questionary
import requests

from ms_login import LpisSSOSession, USER_AGENT


def ask_select(message, choices):
	"""Ask the user to pick one of choices, or return None without a terminal.

	Without a tty (cron, pipes, background runs) questionary cannot render its
	prompt and blows up in prompt_toolkit, so the question is skipped and the
	caller falls back to its default.
	"""
	if not (sys.stdin.isatty() and sys.stdout.isatty()):
		return None
	try:
		return questionary.select(message, choices=choices).ask()
	except Exception as error:
		logger.warning("could not show the selection prompt: %s" % error)
		return None


class BurstDisplay():
	"""Live view of the burst, drawn from a thread of its own.

	The workers must not draw. Thirty threads writing to a terminal fight over
	the GIL and the stdout lock in exactly the second that decides the
	registration - the same reason the page parsing was taken out of them.
	They only record what happened; this redraws that at a fixed rate.

	It writes straight to the terminal instead of going through the logger, so
	no cursor movement ends up in the log file, and it only runs on a real tty.
	"""

	COLOURS = {
		"pending": "\033[2;37m",	# not sent yet
		"inflight": "\033[33m",		# sent, still waiting - orange
		"closed": "\033[31m",		# answered, registration not open yet
		"open": "\033[32m",			# answered and open: the winner
		"stale": "\033[90m",		# landed after somebody had already won
		"failed": "\033[35m",
	}
	RESET = "\033[0m"

	def __init__(self, poller, stream=None, rate=0.1):
		self.poller = poller
		self.stream = stream if stream is not None else sys.stdout
		self.rate = rate
		self._stop = threading.Event()
		self._thread = None
		self._height = 0

	# -- state -------------------------------------------------------- #

	@staticmethod
	def state_of(shot, someone_won):
		if isinstance(shot["outcome"], Exception):
			return "failed"
		if shot["outcome"] is True:
			return "open"
		if shot["outcome"] is False:
			return "stale" if shot["after_win"] else "closed"
		if shot["sent"] is None:
			return "pending"
		return "stale" if someone_won else "inflight"

	@staticmethod
	def cell(shot, state):
		sent = "     --  " if shot["sent"] is None else "T%+.2fs" % shot["sent"]
		took = "      --" if shot["elapsed"] is None else "%6.0f ms" % (shot["elapsed"] * 1000)
		if state == "failed":
			label = "failed"
		elif shot["outcome"] is None:
			label = "sent" if shot["sent"] is not None else ""
		else:
			label = "open" if shot["outcome"] else "closed"
		return "burst %02d  plan T%+.2fs  sent %s  %s  %-6s" % (
			shot["seq"] + 1, shot["planned"], sent, took, label)

	# -- rendering ---------------------------------------------------- #

	def frame(self, width=None):
		shots, someone_won = self.poller.snapshot()
		if not shots:
			return ""
		if width is None:
			width = shutil.get_terminal_size((100, 24)).columns
		cells = []
		for shot in shots:
			state = self.state_of(shot, someone_won)
			cells.append(self.COLOURS.get(state, "") + self.cell(shot, state) + self.RESET)
		plain = self.cell(shots[0], "closed")
		columns = max(1, min(len(cells), width // (len(plain) + 2)))
		rows = -(-len(cells) // columns)
		# filled column by column, so reading straight down follows the grid
		lines = []
		for row in range(rows):
			parts = []
			for column in range(columns):
				index = column * rows + row
				if index < len(cells):
					parts.append(cells[index])
			lines.append("  ".join(parts))
		answered = sum(1 for shot in shots if shot["outcome"] is not None)
		lines.insert(0, "burst: %d sent, %d answered%s"
					 % (len(shots), answered, "" if not someone_won else ", winner found"))
		return "\n".join(lines)

	def draw(self):
		text = self.frame()
		if not text:
			return
		lines = text.split("\n")
		out = []
		if self._height:
			out.append("\033[%dA" % self._height)
		for line in lines:
			out.append("\033[2K" + line + "\n")
		self._height = len(lines)
		self.stream.write("".join(out))
		self.stream.flush()

	# -- lifecycle ---------------------------------------------------- #

	def _loop(self):
		while not self._stop.wait(self.rate):
			try:
				self.draw()
			except Exception:
				# a broken display must never take the burst down with it
				return

	def start(self):
		try:
			self.stream.write("\033[?25l")	# hide the cursor while redrawing
			self.stream.flush()
		except Exception:
			return
		self._thread = threading.Thread(target=self._loop, daemon=True)
		self._thread.start()

	def stop(self):
		self._stop.set()
		if self._thread is not None:
			self._thread.join(timeout=1.0)
		try:
			self.draw()					# one last frame with the final states
			self.stream.write("\033[?25h")
			self.stream.flush()
		except Exception:
			pass


class BurstPoller():
	"""Fires overlapping requests at a page around the second it opens.

	A sequential poll loop gets exactly one attempt per round trip, so whether
	it wins comes down to where its attempts happen to fall relative to the
	opening second: an attempt that leaves a moment too early is answered with
	"not yet possible", and the next one only goes out once that answer came
	back. Firing on a fixed grid instead, with the requests overlapping in
	flight, takes that timing luck out: whatever the round trip turns out to
	be, some request is always in the air when the registration flips open.

	The first response that shows the registration as open wins. Note that
	this is not the same as the first response that arrives - every request
	sent before the opening second comes back perfectly fine, just with a page
	saying the registration has not started. Once there is a winner no further
	requests are sent, and answers still in flight are discarded.
	"""

	def __init__(self, sessions, url, looks_open, confirm=None, interval=0.1,
				 lead=3.0, deadline=10.0, timeout=5.0, live=False):
		self.sessions = sessions
		self.url = url
		# looks_open runs inside the workers and must stay cheap; confirm runs
		# once in the calling thread and has the last word
		self.looks_open = looks_open
		self.confirm = confirm
		self.interval = interval
		self.lead = lead
		self.deadline = deadline
		self.timeout = timeout
		self.live = live
		# seq -> what we know about that shot so far; written twice, once when
		# a worker actually sends it and once when the answer comes back
		self.shots = {}
		self._won = False
		self._guard = threading.Lock()

	def snapshot(self):
		"""A copy of the grid state, safe to read while the burst is running."""
		with self._guard:
			shots = [dict(shot) for shot in
					 sorted(self.shots.values(), key=lambda shot: shot["seq"])]
			return shots, self._won

	def prewarm(self):
		"""Open every connection up front so no burst request pays a handshake.

		mechanize has no keep-alive at all and a fresh TLS connection to LPIS
		costs about as much as two extra round trips, which is the difference
		between a request that lands in the opening second and one that does
		not.
		"""
		bodies = []

		def warm(session):
			try:
				response = session.get(self.url, timeout=self.timeout)
				bodies.append(response.content)
				return True
			except requests.exceptions.RequestException as error:
				logger.warning("could not pre-warm a connection: %s" % error)
				return False

		with ThreadPoolExecutor(max_workers=len(self.sessions)) as pool:
			warmed = sum(1 for ok in pool.map(warm, self.sessions) if ok)
		logger.info("pre-warmed %s/%s connections" % (warmed, len(self.sessions)))

		# The first BeautifulSoup parse in a process costs about 370ms of
		# one-off setup, against 7ms for every one after it. Usually something
		# earlier in the run has already paid that, but leaving a cliff that
		# size to chance on the one parse that decides the registration is not
		# worth it - so it gets paid here, on a page nobody is waiting for.
		if bodies:
			started = time.perf_counter()
			try:
				self.looks_open(bodies[0])
				if self.confirm is not None:
					self.confirm(bodies[0])
			except Exception as error:
				logger.warning("could not pre-warm the parser: %s" % error)
			logger.info("pre-warmed the parser in %.0f ms"
						% ((time.perf_counter() - started) * 1000))
		return warmed

	def run(self, opens_at):
		"""Fire the grid and return the first response that shows it open.

		Returns (session, response, body) or None if the deadline passed
		without the registration ever opening.
		"""
		found = threading.Event()
		candidate = []
		guard = self._guard

		def shoot(seq, session, planned):
			# a request that would go out after somebody already won is simply
			# never sent
			if found.is_set():
				return
			started = time.time()
			with guard:
				self.shots[seq]["sent"] = started - opens_at
			try:
				response = session.get(self.url, timeout=self.timeout)
				body = response.content
			except requests.exceptions.RequestException as error:
				with guard:
					shot = self.shots[seq]
					shot["elapsed"] = time.time() - started
					shot["outcome"] = error
					shot["after_win"] = bool(candidate)
				return
			elapsed = time.time() - started
			# Only the cheap test runs in here. Parsing the page inside a
			# worker holds the GIL against every other worker, and it was
			# measured putting ~400ms between the winning answer arriving and
			# the burst noticing it - on a page far smaller than the real one.
			try:
				hit = self.looks_open(body)
			except Exception as error:
				logger.warning("could not check a burst response: %s" % error)
				hit = False
			with guard:
				shot = self.shots[seq]
				shot["elapsed"] = elapsed
				shot["outcome"] = hit
				# whether somebody else was already there decides how this
				# shot reads: a late "closed" says nothing about the timing
				shot["after_win"] = bool(candidate)
				if hit and not candidate:
					candidate.append((session, response, body))
					self._won = True
					found.set()

		display = BurstDisplay(self) if self.live else None
		pool = ThreadPoolExecutor(max_workers=len(self.sessions))
		# Never start in the past: grid points that are already due carry no
		# delay, so a start behind the clock would dump the whole lead-in on
		# the server at once. Happens whenever the registration is open
		# already, or when getting ready took longer than the lead.
		planned = max(opens_at - self.lead, time.time())
		# Give up deadline seconds after it opens - but always allow a full
		# deadline's worth of tries, so starting late (a slow login, a delayed
		# cron) still gets a real attempt instead of none at all.
		last = max(opens_at, planned) + self.deadline
		index = 0
		if display is not None:
			display.start()
		try:
			while True:
				while not found.is_set() and planned <= last:
					delay = planned - time.time()
					if delay > 0:
						# the grid is 100ms wide, so ordinary sleep accuracy
						# is plenty - no need to burn a core spinning for it
						found.wait(timeout=delay)
					if found.is_set():
						break
					with guard:
						self.shots[index] = {"seq": index, "planned": planned - opens_at,
											 "sent": None, "elapsed": None,
											 "outcome": None, "after_win": False}
					pool.submit(shoot, index, self.sessions[index % len(self.sessions)], planned)
					index += 1
					planned += self.interval
				# the last shots may still be on their way
				if not found.is_set():
					found.wait(timeout=self.timeout)
				if not candidate:
					logger.info("burst: %s requests sent, none showed it open" % index)
					return None
				hit = candidate[0]
				if self.confirm is None or self.confirm(hit[2]):
					logger.info("burst: %s requests sent, won on shot %s"
								% (index, self._winning_seq()))
					return hit
				# The cheap test can match on something else in the page, so a
				# candidate it produced is only a lead. Drop it and keep going.
				logger.info("burst: a response looked open but did not hold up, continuing")
				with guard:
					candidate.clear()
					self._won = False
				found.clear()
				if planned > last:
					logger.info("burst: %s requests sent, none held up" % index)
					return None
		finally:
			if display is not None:
				display.stop()
			# in-flight requests are plain GETs, so there is nothing to clean
			# up and nothing worth waiting for
			pool.shutdown(wait=False)

	def _winning_seq(self):
		with self._guard:
			for shot in sorted(self.shots.values(), key=lambda shot: shot["seq"]):
				if shot["outcome"] is True:
					return shot["seq"] + 1
		return None

	def log_attempts(self):
		"""Log the whole grid relative to the opening second, after the fact.

		This goes through the logger, so it is what ends up in the log file and
		what a run without a terminal shows. Requests that were still in flight
		when somebody won are listed too - they were abandoned on purpose, and
		leaving them out would make it look like the grid had holes in it.
		"""
		shots, someone_won = self.snapshot()
		for shot in shots:
			state = BurstDisplay.state_of(shot, someone_won)
			if state == "pending":
				logger.info("  burst %02d  plan T%+.2fs  never sent" % (shot["seq"] + 1, shot["planned"]))
			elif shot["outcome"] is None:
				logger.info("  burst %02d  plan T%+.2fs  sent T%+.2fs  still in flight when it was won, dropped"
							% (shot["seq"] + 1, shot["planned"], shot["sent"]))
			elif isinstance(shot["outcome"], Exception):
				logger.info("  burst %02d  plan T%+.2fs  sent T%+.2fs  took %6.0f ms  failed: %s"
							% (shot["seq"] + 1, shot["planned"], shot["sent"],
							   shot["elapsed"] * 1000, shot["outcome"]))
			else:
				logger.info("  burst %02d  plan T%+.2fs  sent T%+.2fs  took %6.0f ms  %s"
							% (shot["seq"] + 1, shot["planned"], shot["sent"],
							   shot["elapsed"] * 1000,
							   "open" if shot["outcome"] else
							   ("closed (after the win)" if shot["after_win"] else "closed")))


class WuLpisApi():

	URL = "https://lpis.wu.ac.at/lpis"

	def __init__(self, username=None, password=None, args=None, sessiondir=None):
		self.username = username
		self.password = password
		self.matr_nr = username[1:]
		self.args = args
		self.data = {}
		self.status = {}
		self.browser = mechanize.Browser()

		if sessiondir:
			self.sessionfile = sessiondir + username
		else:
			self.sessionfile = "sessions/" + username

		self.sso = LpisSSOSession(
			username=username,
			password=password,
			sessionfile=self.sessionfile,
			ms_domain=getattr(args, "msdomain", None) or "s.wu.ac.at",
			mfa_method=getattr(args, "mfa_method", None),
		)

		self.browser.set_handle_robots(False)   # ignore robots
		self.browser.set_handle_refresh(False)  # can sometimes hang without this
		self.browser.set_handle_equiv(True)
		self.browser.set_handle_redirect(True)
		self.browser.set_handle_referer(True)
		self.browser.set_debug_http(False)
		self.browser.set_debug_responses(False)
		self.browser.set_debug_redirects(True)
		# share the cookie jar with the requests session used for the SSO login,
		# so mechanize keeps browsing with the session established there
		self.browser.set_cookiejar(self.sso.session.cookies)
		self.browser.addheaders = [
			('User-agent', USER_AGENT),
			('Accept', '*/*')
		]
		self.login()

	def login(self):
		starttime = time.time_ns()
		logger.info("init time: %s" % starttime)
		self.data = {}

		logger.info("logging in %s..." % self.username)

		# LPIS itself has no login form anymore: the whole chain runs over
		# Keycloak (bach-id.wu.ac.at) and Microsoft Entra ID incl. 2FA.
		response = self.sso.login()

		# get scraped LPIS url
		# looks like: https://lpis.wu.ac.at/kdcs/bach-s##/#####/
		url = response.url
		self.URL_scraped = url[:url.rindex('/')+1]

		# hand the already fetched page over to mechanize so the following
		# form handling works exactly as before
		# requests already decoded the body, so the transfer related headers
		# must not be passed on
		skip = ("content-encoding", "content-length", "transfer-encoding")
		headers = [(name, value) for name, value in response.headers.items()
				   if name.lower() not in skip]
		self.browser.set_response(mechanize.make_response(
			response.content, headers, url,
			response.status_code, response.reason or "OK",
		))

		self.data = self.URL_scraped

		if self.sso.used_stored_session and not self.sso.did_interactive_login:
			logger.info("reused stored session (no password/2FA needed)")

		logger.info(f"request time {(time.time_ns() - starttime) / 1000000000}s")

		return self.data


	def getResults(self):
		status = self.status
		if "last_logged_in" in status:
			status["last_logged_in"] = self.status["last_logged_in"].strftime("%Y-%m-%d %H:%M:%S")
		return {
			"data" : self.data, 
			"status" : self.status
		}


	def save_session(self):
		"""Persist the SSO cookies so password and 2FA are only needed once."""
		return self.sso.save_session()


	def load_session(self):
		return self.sso.load_session()


	def logout(self):
		"""Drop the stored session, the next login asks for password and 2FA again."""
		self.sso.clear_session()
		logger.info("stored session removed")


	def infos(self):
		# logger.info "getting data ..."
		self.data = {}
		self.browser.select_form('ea_stupl')
		
		form = self.browser.form

		# Show all possible studies
		sectionpoints = [{"name": x.get_labels()[0].text.strip() if x.get_labels() else '', "value": x.name} for x in form.find_control(form.controls[0].name).get_items() if not x.attrs.get('id') == "abgewaehlt"]

		if not self.args.sectionpoint:
			self.args.sectionpoint = ask_select("select sectionpoint (enter):", sectionpoints)
			if not self.args.sectionpoint:
				logger.info("no sectionpoint selected, using the first one")

		# Select first element in Select Options Dropdown
		item = form.find_control(form.controls[0].name).get(self.args.sectionpoint) if self.args.sectionpoint else form.find_control(form.controls[0].name).get(None ,None, None, 0)
		print("sectionpoint: %s" % item.name)
		item.selected = True
		

		r = self.browser.submit()
		
		self.browser.select_form('ea_stupl')
		form = self.browser.form

		soup = BeautifulSoup(r.read(), "html.parser")

		pp = {}

		total = soup.find('table', {"class" : "b3k-data"}).find('tbody').select('a[href*="DLVO"]').__len__()
		index = 0
		bar_length = 30

		for i, planpunkt in enumerate(soup.find('table', {"class" : "b3k-data"}).find('tbody').find_all('tr')):
			try:
				if not total == 0:
					filled = int(bar_length * ((index + 1) / total))
					bar = "█" * filled + "-" * (bar_length - filled)
					print(f"\r|{bar}| {round((index + 1) / total * 100)}%", end="")

				if planpunkt.select('td:nth-of-type(2)')[0].text:
					key = planpunkt.a['id'][1:]
					pp[key] = {}
					pp[key]["order"] = i + 1
					pp[key]["depth"] = int(re.findall('\\d+', planpunkt.select('td:nth-of-type(1)')[0]['style'])[0]) / 16
					pp[key]["id"] = key
					pp[key]["type"] = planpunkt.select('td:nth-of-type(1) span:nth-of-type(1)')[0].text.strip()
					pp[key]["name"] = planpunkt.select('td:nth-of-type(1) span:nth-of-type(2)')[0].text.strip()
					
					if planpunkt.select('a[href*="DLVO"]'):
						pp[key]["lv_url"] = planpunkt.select('a[href*="DLVO"]')[0]['href']
						pp[key]["lv_status"] = planpunkt.select('a[href*="DLVO"]')[0].text.strip()

					if '/' in planpunkt.select('td:nth-of-type(2)')[0].text:
						pp[key]["attempts"] = planpunkt.select('td:nth-of-type(2) span:nth-of-type(1)')[0].text.strip()
						pp[key]["attempts_max"] = planpunkt.select('td:nth-of-type(2) span:nth-of-type(2)')[0].text.strip()

					if planpunkt.select('td:nth-of-type(3)')[0].text.strip():
						pp[key]["result"] = planpunkt.select('td:nth-of-type(3)')[0].text.strip()
					if planpunkt.select('td:nth-of-type(4)')[0].text.strip():
						pp[key]["date"] = planpunkt.select('td:nth-of-type(4)')[0].text.strip()

					if 'lv_url' in pp[key]:
						index += 1
						r = self.browser.open(self.URL_scraped + pp[key]["lv_url"])
						soup = BeautifulSoup(r.read(), "html.parser")
						pp[key]['lvs'] = {}

						if soup.find('table', {"class" : "b3k-data"}):
							for lv in soup.find('table', {"class" : "b3k-data"}).find('tbody').find_all('tr'):
								try:
									number = lv.select('.ver_id a')[0].text.strip()
									pp[key]['lvs'][number] = {}
									pp[key]['lvs'][number]['id'] = number
									pp[key]['lvs'][number]['semester'] = lv.select('.ver_id span')[0].text.strip()
									pp[key]['lvs'][number]['prof'] = lv.select('.ver_title div')[0].text.strip()
									pp[key]['lvs'][number]['name'] = lv.find('td', {"class" : "ver_title"}).findAll(text=True, recursive=False)[1].strip()
									pp[key]['lvs'][number]['status'] = lv.select('td.box div')[0].text.strip()
									capacity = lv.select('div[class*="capacity_entry"]')[0].text.strip()
									pp[key]['lvs'][number]['free'] = capacity[:capacity.rindex('/')-1]
									pp[key]['lvs'][number]['capacity'] = capacity[capacity.rindex('/')+2:]
									
									if lv.select('td.action form'):
										internal_id = lv.select('td.action form')[0]['name']
										pp[key]['lvs'][number]['internal_id'] = internal_id.rsplit('_')[1]
									date = e.text.strip() if (e := lv.select_one('td.action .timestamp span')) else None
									
									if 'ab' in date:
										pp[key]['lvs'][number]['date_start'] = date[3:]
									if 'bis' in date:
										pp[key]['lvs'][number]['date_end'] = date[4:]

									if lv.select('td.box.active'):
										pp[key]['lvs'][number]['registerd_at'] = lv.select('td.box.active .timestamp span')[0].text.strip()

									if lv.select('td.capacity div[title*="Anzahl Warteliste"]'):
										pp[key]['lvs'][number]['waitlist'] = lv.select('td.capacity div[title*="Anzahl Warteliste"]')[0].text.strip()
								except Exception:
									continue
			except Exception as e:
				logger.opt(colors=True).error("Error parsing planpunkt: %s" % str(e))
		
		# clear bar
		print("\r" + " " * (bar_length + 10) + "\r", end="")
		
		# lv_index = 0

		# lv_register = []

		for pp_id in pp:
			print(f"{'   ' * int(pp[pp_id]['depth'])}{pp_id} {pp[pp_id]['name']}")
			if "lvs" in pp[pp_id] and "" in pp[pp_id]["lvs"]:
				print(f"\033[94m{'   ' * int(pp[pp_id]['depth'] + 1)}{pp[pp_id]['lv_status']}\033[0m")
			elif "lvs" in pp[pp_id]:
				for lv_id in pp[pp_id]["lvs"]:
					# lv_index += 1
					lv = pp[pp_id]["lvs"][lv_id]
					# lv_register.append({"lv": lv_id, "pp": pp_id, "name": pp[pp_id]["name"]})
					print(f"{'   ' * int(pp[pp_id]['depth'] + 1)}", end="")

					print("\033[91m" if int(lv["free"]) == 0 or lv["status"] == "Anmeldung nicht möglich" else "\033[92m", end="")
					if "date_start" in lv:
						print("\033[93m", end="")

					# print("[{:03d}] {:<3} {:<4} - {:<9} {:<25} {:>4}/{:<4} {:<27}".format(lv_index, pp[pp_id]["type"], lv["id"], lv["semester"], lv["prof"][0:25], lv["free"], lv["capacity"], lv["status"]), end="") # with index
					print("{:<3} {:<4} - {:<9} {:<25} {:>4}/{:<4} {:<27}".format(pp[pp_id]["type"], lv["id"], lv["semester"], lv["prof"][0:25], lv["free"], lv["capacity"], lv["status"]), end="")


					print(f"(Anmeldung ab: {lv['date_start']})" if "date_start" in lv else "", end="")
					print(f"(Anmeldung bis: {lv['date_end']})" if "date_end" in lv else "", end="")
					
					print("\033[0m")

							
		self.data['pp'] = pp				
		return self.data


	def _burst_sessions(self, count):
		"""Build independent sessions for the burst.

		They cannot all share one session: its connection pool would serialise
		the requests we are trying to overlap, and its cookie jar is not safe
		to mutate from several threads at once. Each worker therefore gets its
		own session, seeded with a copy of the cookies the login produced.
		"""
		sessions = []
		for _ in range(count):
			session = requests.Session()
			session.headers.update(self.sso.session.headers)
			for cookie in self.sso.session.cookies:
				session.cookies.set_cookie(copy.copy(cookie))
			adapter = requests.adapters.HTTPAdapter(pool_connections=2, pool_maxsize=2)
			session.mount("https://", adapter)
			session.mount("http://", adapter)
			sessions.append(session)
		return sessions

	def _hand_to_mechanize(self, response, body, url):
		"""Make a response fetched with requests the browser's current page."""
		skip = ("content-encoding", "content-length", "transfer-encoding")
		headers = [(name, value) for name, value in response.headers.items()
				   if name.lower() not in skip]
		self.browser.set_response(mechanize.make_response(
			body, headers, url, response.status_code, response.reason or "OK",
		))

	def _submit_current_form(self, session, timeout=15.0):
		"""Send the selected mechanize form over the session that won the burst.

		Using the winning session keeps the cookies consistent with the page
		the form came from, and its connection is already open. mechanize
		still builds the request - hidden fields and all - only the sending is
		done by requests.
		"""
		try:
			request = self.browser.form.click()
			target = request.get_full_url()
			method = request.get_method()
			data = request.data
			headers = {name: value for name, value in request.header_items()
					   if name.lower() != "content-length"}
		except Exception as error:
			# nothing has been sent yet, so falling back to mechanize is safe
			logger.warning("could not prepare the fast submit (%s), using mechanize"
						   % error)
			return self.browser.submit().read()
		# deliberately no fallback past this point: once the request is out we
		# cannot tell whether the server processed it, and a blind retry could
		# register twice
		if method == "POST":
			response = session.post(target, data=data, headers=headers, timeout=timeout)
		else:
			response = session.get(target, headers=headers, timeout=timeout)
		body = response.content
		self._hand_to_mechanize(response, body, response.url)
		return body

	def registration(self):

		self.browser.select_form('ea_stupl')
		
		form = self.browser.form
		# Select first element in Select Options Dropdown
		item = form.find_control(form.controls[0].name).get(self.args.sectionpoint) if self.args.sectionpoint else form.find_control(form.controls[0].name).get(None ,None, None, 0)
		logger.info("sectionpoint: %s" % item.name)
		item.selected = True
		
		timeserver = "timeserver.wu.ac.at"
		logger.info("syncing time with %s" % timeserver)

		# # timeserver sync
		c = ntplib.NTPClient()
		response = c.request(timeserver, version=3)
		logger.info("time difference: %.10f (difference is taken into account)" % response.offset)

		# The burst blankets the opening second from every side, so there is no
		# single moment left to aim at and --offset has nothing to do anymore.
		# The clock difference still matters: it says where that second
		# actually lies on this machine's clock.
		clock_offset = response.offset
		if getattr(self.args, "offset_explicit", False):
			logger.opt(colors=True).info(
				"<yellow>--offset %.2f is ignored: the burst covers the whole "
				"window instead of firing at one point</yellow>" % self.args.offset)
		if self.args.planobject and self.args.course:
			pp = "S" + self.args.planobject
			lv = self.args.course
			lv2 = self.args.course2 or lv
		
		self.data = {}
		self.browser.select_form('ea_stupl')
		r = self.browser.submit()
		soup = BeautifulSoup(r.read(), "html.parser")
		url = soup.find('table', {"class" : "b3k-data"}).find('a', id=pp).parent.findAll('a', href=True, title="Lehrveranstaltungsanmeldung")[0]["href"]
		r = self.browser.open(self.URL_scraped + url)

		soup = BeautifulSoup(r.read(), "html.parser")

		if not soup.find('table', {"class" : "b3k-data"}).find('a', text=lv) or not soup.find('table', {"class" : "b3k-data"}).find('a', text=lv2):
			logger.opt(colors=True).error("<red>lv %s or %s not found</red>" % (lv, lv2))
			logger.opt(colors=True).info("<yellow>check if the course is available in lpis</yellow>")
			return

		lv_url = self.URL_scraped + url

		course = lv.encode()

		def looks_open(body):
			"""Cheap raw-bytes test, run inside the burst workers.

			It looks for the open marker between our course number and the end
			of its table row, so another course further down the page being
			open does not set it off. It may still produce the odd false
			alarm, which is what confirm_open is for - what it must never do
			is miss a real one, and it cannot: the marker has to be in that
			row for the row to be registrable.
			"""
			start = body.find(course)
			if start < 0:
				return False
			end = body.find(b'</tr>', start)
			return body.find(b'possible', start, end if end > 0 else len(body)) >= 0

		def confirm_open(body):
			"""The authoritative check, run once in the calling thread."""
			page = BeautifulSoup(body, "html.parser")
			entry = page.find('table', {"class" : "b3k-data"}).find('a', text=lv)
			return bool(entry and entry.parent.parent.select('div.box.possible'))

		date = soup.find('table', {"class" : "b3k-data"}).find('a', text=lv).parent.parent.select('.action .timestamp span')[0].text.strip()
		if 'ab' in date:
			# where the opening second sits on this machine's clock
			opens_at = time.mktime(datetime.datetime.strptime(date[3:], "%d.%m.%Y %H:%M").timetuple()) - clock_offset

			if (opens_at - time.time()) > 600:
				logger.opt(colors=True).info("<yellow>registration starts in more than 10 minutes</yellow>")
				logger.opt(colors=True).info("<green>waiting until 5 minutes before the registration starts</green>")
				login_triggertime = opens_at - 300
				while time.time() < login_triggertime:
					remaining_time = login_triggertime - time.time()
					hours, remainder = divmod(remaining_time, 3600)
					minutes, seconds = divmod(remainder, 60)
					print("logging in again in: {:02d}:{:02d}:{:04.1f}".format(int(hours), int(minutes), seconds), end="\r")
					time.sleep(0.1)
				self.login()
				self.registration()
				return
		else:
			# already open (or no start time given): fire straight away
			opens_at = time.time()

		poller = BurstPoller(
			sessions=self._burst_sessions(self.args.burst_workers),
			url=lv_url,
			looks_open=looks_open,
			confirm=confirm_open,
			interval=self.args.burst_interval,
			lead=self.args.burst_lead,
			deadline=self.args.burst_deadline,
			# the live grid is cursor movement and colour, which only makes
			# sense on a terminal - under cron or a pipe the log block below
			# is the whole story
			live=sys.stdout.isatty(),
		)

		# Get every connection open while there is still time to spare. The
		# head start is generous on purpose: a pre-warm that runs into its own
		# timeout leaves connections cold, and those then pay the handshake
		# during the burst - which is the one moment it must not be paid.
		prewarm_at = opens_at - self.args.burst_lead - 15
		while time.time() < prewarm_at:
			remaining_time = prewarm_at - time.time()
			hours, remainder = divmod(remaining_time, 3600)
			minutes, seconds = divmod(remainder, 60)
			print("starting in: {:02d}:{:02d}:{:05.2f}".format(int(hours), int(minutes), seconds), end="\r")
			time.sleep(0.05)
		poller.prewarm()

		logger.info("opens at: %s (clock difference %.3fs)"
					% (time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(opens_at)), clock_offset))
		logger.opt(colors=True).info(
			"<green>bursting from T-%.1fs every %.2fs across %s connections</green>"
			% (self.args.burst_lead, self.args.burst_interval, self.args.burst_workers))

		result = poller.run(opens_at)
		poller.log_attempts()

		if not result:
			logger.opt(colors=True).error(
				"<red>registration did not open within %.0fs after %s</red>"
				% (self.args.burst_deadline, time.strftime("%H:%M:%S", time.localtime(opens_at))))
			return

		session, response, body = result
		# The burst sessions carry copies of the cookies, so anything LPIS set
		# during the burst only exists on the one that won. Folding it back
		# keeps the browser's jar - and with it every later request - in step
		# with the page we are about to submit from.
		for cookie in session.cookies:
			self.sso.session.cookies.set_cookie(copy.copy(cookie))
		# the form handling below runs on the browser, so the page it should
		# work on has to be handed over
		self._hand_to_mechanize(response, body, lv_url)
		soup = BeautifulSoup(body, "html.parser")

		logger.info("final open time start: %s" % datetime.datetime.now())

		# Submit registration until it was successful
		while True:

			logger.info("final open time end: %s" % datetime.datetime.now())
			logger.opt(colors=True).info("<green>registration is possible</green>")

			cap1 = soup.find('table', {"class" : "b3k-data"}).find('a', text=lv).parent.parent.select('div[class*="capacity_entry"]')[0].text.strip()
			cap2 = soup.find('table', {"class" : "b3k-data"}).find('a', text=lv2).parent.parent.select('div[class*="capacity_entry"]')[0].text.strip()
			free1 = int(cap1[:cap1.rindex('/')-1])
			free2 = int(cap2[:cap2.rindex('/')-1])

			form1 = soup.find('table', {"class" : "b3k-data"}).find('a', text=lv).parent.parent.select('.action form')[0]["name"].strip()
			form2 = soup.find('table', {"class" : "b3k-data"}).find('a', text=lv2).parent.parent.select('.action form')[0]["name"].strip()

			logger.info("end time: %s" % datetime.datetime.now())
			logger.opt(colors=True).info("<green>freie plaetze: lv1: %s, lv2: %s (if defined)</green>" % (free1, free2))
			if free1 > 0:
				if not form1.startswith("WLDEL"):
					self.browser.select_form(form1)
					logger.info("submitting registration form1 (%s)" % form1)
				else:
					logger.info("skipping form1 (%s)" % form1)
			elif lv2:
				if not form2.startswith("WLDEL"):
					self.browser.select_form(form2)
					logger.info("submitting registration form2 (%s)" % form2)
				else:
					logger.info("skipping form2 (%s)" % form2)

			body = self._submit_current_form(session)

			soup = BeautifulSoup(body, "html.parser")

			alert_content = soup.find('div', {"class" : 'b3k_alert_content'})
			
			# Check if alert_content is available + check if registration failed
			if alert_content and "nicht" in alert_content.text.strip() and "Warteliste" not in alert_content.text.strip():
				logger.opt(colors=True).error('<red>%s</red>' % alert_content.text.strip())
			
			if alert_content:
				alert_text = alert_content.text.strip()
				logger.opt(colors=True).info("<bold>" + alert_text + "</bold>")
				lv = soup.find('table', {"class" : "b3k-data"}).find('a', text=lv).parent.parent
				logger.info("Frei: " + lv.select('div[class*="capacity_entry"]')[0].text.strip())
				wl_title = "Anzahl Warteliste" if not "Warteliste" in alert_text else "aktuelle Wartelistenposition / Anzahl Wartelisteneinträge"
				if lv.select('td.capacity div[title*="%s"]' % wl_title):
					logger.info("Warteliste: " + lv.select('td.capacity div[title*="%s"] span' % wl_title)[0].text.strip() + " / " + lv.select('td.capacity div[title*="%s"] span' % wl_title)[0].text.strip())
					if free1 > 0:
						try:
							if not form2.startswith("WLDEL"):
								self.browser.select_form(form2)
								logger.info("submitting registration form2 (%s)" % form2)
								self._submit_current_form(session)
							else:
								logger.info("skipping form2 (%s)" % form2)
						except:
							logger.info("could not submit form (%s)" % form2)
				# ntfy
				try:
					requests.post("https://ntfy.sh/lpis-%s" % self.username, data=alert_text.encode(encoding='utf-8'))
					requests.post("https://ntfy.sh/lpis-bot", data=("[%s]: %s" % (self.username, alert_text)).encode(encoding='utf-8'))
				except:
					pass

			if soup.find('h3'):
				logger.info(soup.find('h3').find('span').text.strip())

			break

	def grades(self):
		"""
		Parse the LPIS "Noten" (grades) page and return a list of grade entries.

		Each entry is a dict with keys:
		  - entry_id: str (e.g., "E24906699")
		  - exam_type: str (short type like "FPm", "FPs", "LVP", "PI", etc.)
		  - exam_type_title: str (full title from the span title attribute, e.g., "Fachprüfung (mündlich)")
		  - title: str (course/exam title)
		  - professor: str or "" (lecturer line, if present)
		  - sst: float or None
		  - ects: float or None
		  - grade_text: str (e.g., "sehr gut", "befriedigend", "mit Erfolg teilgenommen", "nicht genügend")
		  - grade_date: str (DD.MM.YYYY)
		  - study: str (short study name shown, e.g., "BaWiRe-23")
		  - study_title: str (full study title from the title attribute)
		  - row_class: str (CSS class on the <tr>, e.g., "td0", "td1")
		  - outdated: bool (True if row has class "outdated")
		  - outdated_reason: str or "" (from tr["title"], if present)
		"""
		r = self.browser.open(self.URL_scraped + "NT")
		soup = BeautifulSoup(r.read(), "html.parser")

		def _txt(x):
			return x.get_text(strip=True) if x else ""

		def _to_float(x):
			x = (x or "").strip()
			if not x or x.upper() == "N/A":
				return None
			try:
				# numbers appear with dot as decimal separator in the HTML
				return float(x.replace(",", "."))
			except ValueError:
				return None

		grades_list = []

		table = soup.find("table", {"class": "b3k-data"})

		for tr in table.tbody.find_all("tr", recursive=False):
			# Basic row metadata
			row_classes = tr.get("class", [])
			row_class = " ".join([c for c in row_classes if c])
			outdated = "outdated" in row_classes
			outdated_reason = tr.get("title", "") if outdated else ""

			tds = tr.find_all("td", recursive=False)
			if len(tds) < 4:
				continue

			# --- Column 1: Title/Type/Professor ---
			td_title = tds[0]
			anchor = td_title.find("a")
			entry_id = anchor.get("id", "") if anchor else ""

			type_span = td_title.find("b")
			exam_type_el = type_span.find("span") if type_span else None
			exam_type = _txt(exam_type_el)
			exam_type_title = exam_type_el.get("title", "") if exam_type_el else ""

			# The actual title is the next span after the bold span
			title_span = None
			spans = td_title.find_all("span", recursive=False)
			if spans:
				# by inspection, the first span (inside <b>) is exam type, the second span is title
				title_span = spans[-1] if len(spans) >= 1 else None
			title = _txt(title_span)

			# Professor line lives as text after a <br/>
			# Robust approach: get all direct text nodes after the first <br/> and strip
			professor = ""
			# collect the text nodes that are not inside <span>/<b>
			# Often the professor line contains multiple non-breaking spaces; normalize spaces
			for br in td_title.find_all("br"):
				# take text immediately following this br
				if br.next_sibling and isinstance(br.next_sibling, str):
					professor = br.next_sibling.strip()
				else:
					# sometimes wrapped in tags
					sib = br.find_next_sibling(text=True)
					if sib:
						professor = sib.strip()
				if professor:
					# replace multiple spaces / non-breaking spaces
					professor = re.sub(r"\s+", " ", professor)
					break

			# --- Column 2: SSt / ECTS ---
			td_sst_ects = tds[1]
			divs = td_sst_ects.find_all("div", recursive=False)
			sst = _to_float(_txt(divs[0]) if len(divs) >= 1 else "")
			ects = _to_float(_txt(divs[1]) if len(divs) >= 2 else "")

			# --- Column 3: Grade text and date ---
			td_grade = tds[2]
			grade_spans = td_grade.find_all("span", recursive=False)
			grade_text = _txt(grade_spans[0]) if len(grade_spans) >= 1 else ""
			grade_date = _txt(grade_spans[1]) if len(grade_spans) >= 2 else ""

			# --- Column 4: Study ---
			td_study = tds[3]
			study = _txt(td_study)
			study_title = td_study.get("title", "")

			grades_list.append({
				"entry_id": entry_id,
				"exam_type": exam_type,
				"exam_type_title": exam_type_title,
				"title": title,
				"professor": professor,
				"sst": sst,
				"ects": ects,
				"grade_text": grade_text,
				"grade_date": grade_date,
				"study": study,
				"study_title": study_title,
				"row_class": row_class,
				"outdated": outdated,
				"outdated_reason": outdated_reason,
			})

			# Pretty-print grades as a simple fixed-width table
			if len(grades_list) == 1:
				# print header once when seeing the first row
				header = (
					f"{'Typ':<4} "
					f"{'Titel':<50} "
					f"{'Professor:in':<24} "
					f"{'ECTS':>5} "
					f"{'Note':<18} "
					f"{'Datum':<10} "
					f"{'Studium':<12}"
				)
				print(header)
				print("-" * len(header))

			# helpers (local, lightweight)
			_clip = lambda s, n: (s or "") if len(s or "") <= n else (s or "")[: max(0, n - 1)] + "…"
			_fmt = lambda x: "" if x is None else ("%g" % x)

			row = (
				f"{_clip(exam_type, 4):<4} "
				f"{_clip(title, 50):<50} "
				f"{_clip(professor, 24):<24} "
				f"{_fmt(ects):>5} "
				f"{_clip(grade_text, 18):<18} "
				f"{_clip(grade_date, 10):<10} "
				f"{_clip(study, 12):<12}"
			)
			print(row)
		
		def _grade_to_numeric(txt: str):
			if not txt:
				return None
			t = txt.strip().lower()
			# Map common German grade texts to Austrian numeric scale
			if "sehr gut" in t:
				return 1.0
			if t == "gut" or "\xA0gut" in t:  # normalize NBSP edge-cases
				return 2.0
			if "befriedigend" in t:
				return 3.0
			if "genügend" in t and "nicht" not in t:
				return 4.0
			if "nicht genügend" in t:
				# Exclude failing grades entirely from GPA/ECTS calculations
				return None
			# Non-numeric/pass grades (e.g., "mit Erfolg teilgenommen") do not affect GPA
			return None

		def _parse_date(d: str):
			# Expect DD.MM.YYYY, ignore if malformed
			if d == "vorläufig":
				# return todays date
				return datetime.date.today()
			try:
				return datetime.datetime.strptime(d, "%d.%m.%Y").date()
			except Exception:
				return None

		def _semester_key(dt: datetime.date):
			# WS YYYY spans 1.10.YYYY–28/29.02.YYYY+1; SS YYYY spans 1.3.YYYY–30.9.YYYY
			if not dt:
				return None
			if dt.month >= 10 or dt.month <= 2:
				# Winter semester labeled by its starting year
				start_year = dt.year if dt.month >= 10 else dt.year - 1
				return ("WS", start_year)
			else:
				# Summer semester labeled by calendar year
				return ("SS", dt.year)

		def _year_key(dt: datetime.date):
			# Academic year starts 1.10.
			if not dt:
				return None
			start_year = dt.year if dt.month >= 10 else dt.year - 1
			return start_year  # represent AY as its starting year

		# First pass: compute per-study aggregations and keep per-row meta by study
		stats_by_study = {}
		rows_by_study = {}
		for g in grades_list:
			study = g.get("study") or ""
			study_title = g.get("study_title") or ""
			dt = _parse_date(g.get("grade_date"))
			num = _grade_to_numeric(g.get("grade_text"))
			ects = g.get("ects") or 0.0
			sem_k = _semester_key(dt)
			year_k = _year_key(dt)
			st = stats_by_study.setdefault(study, {
				"title": study_title,
				"total_w": 0.0,
				"total_gw": 0.0,
				"per_sem": {},
				"per_year": {},
			})
			# track last seen title (if varies slightly)
			if study_title:
				st["title"] = study_title
			rows_by_study.setdefault(study, []).append({
				"g": g,
				"sem_k": sem_k,
				"year_k": year_k,
			})
			if num is None or ects is None or ects <= 0:
				continue
			st["total_w"] += ects
			st["total_gw"] += num * ects
			if sem_k:
				acc = st["per_sem"].setdefault(sem_k, {"ects": 0.0, "gw": 0.0, "items": []})
				acc["ects"] += ects
				acc["gw"] += num * ects
				acc["items"].append((num, ects))
			if year_k is not None:
				accy = st["per_year"].setdefault(year_k, {"ects": 0.0, "gw": 0.0, "items": []})
				accy["ects"] += ects
				accy["gw"] += num * ects
				accy["items"].append((num, ects))

		def _fmt_gpa(ects_sum, gw_sum):
			if ects_sum and ects_sum > 0:
				return f"{gw_sum/ects_sum:.2f}"
			return "n/a"

		# Sort semesters chronologically: by (year, term order with WS before SS of same AY)
		def _sem_sort_key(k):
			term, y = k
			# Order by start date: WS y starts at Oct y; SS y starts Mar y
			start = datetime.date(y, 10, 1) if term == "WS" else datetime.date(y, 3, 1)
			return start

		def _best_cap_gpa(items, cap):
			# items: list of (num_grade, ects) where lower num is better
			if not items:
				return "n/a"
			rem = float(cap)
			gw = 0.0
			w = 0.0
			for num, e in sorted(items, key=lambda x: x[0]):
				if rem <= 0:
					break
				if e <= 0:
					continue
				take = e if e <= rem else rem
				gw += num * take
				w += take
				rem -= take
			if w <= 0:
				return "n/a"
			return f"{gw/w:.3f}"

		# Display per-study summaries and per-row ECTS for that study
		if stats_by_study:
			print("")
			print("GPA by Study (ECTS-weighted)")
			print("-----------------------------")
			for study in sorted(stats_by_study.keys()):
				st = stats_by_study[study]
				study_header = study if not st.get("title") else f"{study} — {st['title']}"
				print(study_header)
				print(f"  Total: GPA={_fmt_gpa(st['total_w'], st['total_gw'])}  ECTS={st['total_w']:g}")
				if st["per_sem"]:
					print("  Semesters:")
					for k in sorted(st["per_sem"].keys(), key=_sem_sort_key):
						acc = st["per_sem"][k]
						term, y = k
						label = f"{term} {y}"
						line = f"    {label}: GPA={_fmt_gpa(acc['ects'], acc['gw'])}  ECTS={acc['ects']:g}"
						if acc.get('ects', 0.0) > 30 and acc.get('items'):
							best = _best_cap_gpa(acc['items'], 30)
							line += f"  (Best30={best})"
						print(line)
				if st["per_year"]:
					print("  Years (AY):")
					for y in sorted(st["per_year"].keys()):
						acc = st["per_year"][y]
						label = f"{y}/{str(y+1)[-2:]}"
						line = f"    {label}: GPA={_fmt_gpa(acc['ects'], acc['gw'])}  ECTS={acc['ects']:g}"
						if acc.get('ects', 0.0) > 52 and acc.get('items'):
							best = _best_cap_gpa(acc['items'], 52)
							line += f"  (Best52={best})"
						print(line)
				print("-----------------------------")
				
			return grades_list
