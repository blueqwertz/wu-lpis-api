#!/usr/bin/env python
# -*- coding: utf-8 -*-

import datetime, re, os, time, pickle, sys
from lxml import html
from bs4 import BeautifulSoup
import logging
import mechanize, time
import ntplib
from logger import logger
import questionary
import requests

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

		self.browser.set_handle_robots(False)   # ignore robots
		self.browser.set_handle_refresh(False)  # can sometimes hang without this
		self.browser.set_handle_equiv(True)
		self.browser.set_handle_redirect(True)
		self.browser.set_handle_referer(True)
		self.browser.set_debug_http(False)
		self.browser.set_debug_responses(False)
		self.browser.set_debug_redirects(True)
		self.browser.addheaders = [
			('User-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/68.0.3440.106 Safari/537.36'),
			('Accept', '*/*')
		]
		self.login()

	def login(self):
		starttime = time.time_ns()
		logger.info("init time: %s" % starttime)
		self.data = {}

		#if not self.load_session():
		logger.info("logging in %s..." % self.username)

		r = self.browser.open(self.URL)
		self.browser.select_form('login')

		tree = html.fromstring(r.read()) # removes comments from html
		input_username = list(set(tree.xpath("//input[@accesskey='u']/@name")))[0]
		input_password = list(set(tree.xpath("//input[@accesskey='p']/@name")))[0]

		self.browser[input_username] = self.username
		self.browser[input_password] = self.password
		r = self.browser.submit()

		# get scraped LPIS url 
		# looks like: https://lpis.wu.ac.at/kdcs/bach-s##/#####/
		url = r.geturl()

		self.URL_scraped = url[:url.rindex('/')+1]

		self.data = self.URL_scraped
		#self.save_session()

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
		# logger.info "trying to save session ..."
		if not os.path.exists(os.path.dirname(self.sessionfile)):
			try:
				os.makedirs(os.path.dirname(self.sessionfile))
			except:
				raise
		with open(self.sessionfile, 'wb') as file:
			try:
				# dill.dump(self.browser, file)
				pickle.dump(self.browser, file, pickle.HIGHEST_PROTOCOL)
			except:
				return False
		# logger.info "session saved to file ..."
		return True


	def load_session(self):
		# logger.info "trying to load session ..."
		if os.path.isfile(self.sessionfile):
			with open(self.sessionfile, 'rb') as file:
				try:
					self.browser = pickle.load(file)
				except:
					return False
			# logger.info "session loaded from file ..."
			return True


	def infos(self):
		# logger.info "getting data ..."
		self.data = {}
		self.browser.select_form('ea_stupl')
		
		form = self.browser.form

		# Show all possible studies
		sectionpoints = [{"name": x.get_labels()[0].text.strip() if x.get_labels() else '', "value": x.name} for x in form.find_control(form.controls[0].name).get_items() if not x.attrs.get('id') == "abgewaehlt"]

		if not self.args.sectionpoint:
			self.args.sectionpoint = questionary.select("select sectionpoint (enter):",choices=sectionpoints).ask()

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
							date = lv.select('td.action .timestamp span')[0].text.strip()
							
							if 'ab' in date:
								pp[key]['lvs'][number]['date_start'] = date[3:]
							if 'bis' in date:
								pp[key]['lvs'][number]['date_end'] = date[4:]

							if lv.select('td.box.active'):
								pp[key]['lvs'][number]['registerd_at'] = lv.select('td.box.active .timestamp span')[0].text.strip()

							if lv.select('td.capacity div[title*="Anzahl Warteliste"]'):
								pp[key]['lvs'][number]['waitlist'] = lv.select('td.capacity div[title*="Anzahl Warteliste"]')[0].text.strip()
		
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

		offset = 0.80 + response.offset	# seconds before start time when the request should be made
		logger.info("offset: %.2f" % offset)
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

		triggertime = 0
		soup = BeautifulSoup(r.read(), "html.parser")

		if not soup.find('table', {"class" : "b3k-data"}).find('a', text=lv) or not soup.find('table', {"class" : "b3k-data"}).find('a', text=lv2):
			logger.opt(colors=True).error("<red>lv %s or %s not found</red>" % (lv, lv2))
			logger.opt(colors=True).info("<yellow>check if the course is available in lpis</yellow>")
			return

		try:
			requests.post("https://ntfy.sh/lpis-%s" % self.username, data=("starting lpis-api for lv %s (backup: %s)" % (lv, lv2)).encode(encoding='utf-8'))
			requests.post("https://ntfy.sh/lpis-bot", data=("[%s]: starting lpis-api for lv %s (backup: %s)" % (self.username, lv, lv2)).encode(encoding='utf-8'))
		except Exception:
			pass

		date = soup.find('table', {"class" : "b3k-data"}).find('a', text=lv).parent.parent.select('.action .timestamp span')[0].text.strip()
		if 'ab' in date:
			triggertime = time.mktime(datetime.datetime.strptime(date[3:], "%d.%m.%Y %H:%M").timetuple()) - offset

			if (time.mktime(datetime.datetime.strptime(date[3:], "%d.%m.%Y %H:%M").timetuple()) - time.time()) > 600:
				logger.opt(colors=True).info("<yellow>registration starts in more than 10 minutes</yellow>")
				logger.opt(colors=True).info("<green>waiting until 5 minutes before the registration starts</green>")
				login_triggertime = time.mktime(datetime.datetime.strptime(date[3:], "%d.%m.%Y %H:%M").timetuple()) - 300
				while time.time() < login_triggertime:
					remaining_time = login_triggertime - time.time()
					hours, remainder = divmod(remaining_time, 3600)
					minutes, seconds = divmod(remainder, 60)
					print("logging in again in: {:02d}:{:02d}:{:04.1f}".format(int(hours), int(minutes), seconds), end="\r")
					time.sleep(0.1)
				self.login()
				self.registration()
				return

			if triggertime > time.time():
				logger.info("waiting until: %s (%ss)" % (time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(triggertime)), triggertime))
				while time.time() < triggertime:
					remaining_time = triggertime - time.time()
					hours, remainder = divmod(remaining_time, 3600)
					minutes, seconds = divmod(remainder, 60)
					print("starting in: {:02d}:{:02d}:{:05.2f}".format(int(hours), int(minutes), seconds), end="\r")				
				# time.sleep( triggertime - time.time() )

		logger.info("triggertime: %s" % triggertime)
		logger.info("final open time start: %s" % datetime.datetime.now())
		
		# Submit registration until it was successful
		while True:
	
			# Reload page until registration is possible
			while True:
				starttime = time.time_ns()
				logger.opt(colors=True).info("<green>start request %s</green>" % datetime.datetime.now())
				r = self.browser.open(self.URL_scraped + url)
				logger.opt(colors=True).info("<green>end request %s</green>" % datetime.datetime.now())
				logger.info(f"request time {(time.time_ns() - starttime) / 1000000000}s")
				soup = BeautifulSoup(r.read(), "html.parser")
				if soup.find('table', {"class" : "b3k-data"}).find('a', text=lv).parent.parent.select('div.box.possible'):
					# break out of loop to start registration progress
					break
				else:
					logger.opt(colors=True).info("<green>parsing done %s</green>" % datetime.datetime.now())
				logger.opt(colors=True).info("<yellow>registration is not (yet) possibe, waiting ...</yellow>")
				logger.opt(colors=True).info("<yellow>reloading page and waiting for form to be submittable</yellow>")

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

			r = self.browser.submit()

			soup = BeautifulSoup(r.read(), "html.parser")

			alert_content = soup.find('div', {"class" : 'b3k_alert_content'})
			
			# Check if alert_content is available + check if registration failed
			if alert_content and "nicht" in alert_content.text.strip() and "Warteliste" not in alert_content.text.strip():
				logger.opt(colors=True).info('<red>%s</red>' % alert_content.text.strip())
			
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
								r = self.browser.submit()
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
