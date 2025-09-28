#!/usr/bin/env python
# -*- coding: utf-8 -*-

import datetime, re, os, time, pickle, sys
from lxml import html
from bs4 import BeautifulSoup
import mechanize, time
import ntplib
from logger import logger
import questionary

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
			self.args.sectionpoint = questionary.select("select sectionpoint:",choices=sectionpoints).ask()

		# Select first element in Select Options Dropdown
		item = form.find_control(form.controls[0].name).get(self.args.sectionpoint) if self.args.sectionpoint else form.find_control(form.controls[0].name).get(None ,None, None, 0)
		print("sectionpoint: %s" % item.name)
		item.selected = True
		

		r = self.browser.submit()
		
		self.browser.select_form('ea_stupl')
		form = self.browser.form

		soup = BeautifulSoup(r.read(), "html.parser")

		studies = {}
		index = 0
		for i, entry in enumerate(soup.find('select', {'name': form.controls[0].name}).find_all('option')):

			if len(entry.text.split('/')) == 1:
				studies[index] = {}
				studies[index]['id'] = entry['value']
				studies[index]['title'] = entry['title']
				studies[index]['name'] = entry.text
				index += 1

			# if len(entry.text.split('/')) == 1:
			# 	studies[i] = {}
			# 	studies[i]['id'] = entry['value']
			# 	studies[i]['title'] = entry['title']
			# 	studies[i]['name'] = entry.text
			# 	studies[i]['abschnitte'] = {}
			# elif len(entry.text.split('/')) == 2 and entry.text.split('/')[0] == studies[(i-1) % len(studies)]['name']:
			# # elif len(entry.text.split('/')) == 2 and entry.text.split('/')[0] == studies[(i-1) % len(studies)]['name']:
			# 	studies[(i-1) % len(studies)]['abschnitte'][entry['value']] = {}
			# 	studies[(i-1) % len(studies)]['abschnitte'][entry['value']]['id'] = entry['value']
			# 	studies[(i-1) % len(studies)]['abschnitte'][entry['value']]['title'] = entry['title']
			# 	studies[(i-1) % len(studies)]['abschnitte'][entry['value']]['name'] = entry.text

		self.data['studies'] = studies

		pp = {}
		for i, planpunkt in enumerate(soup.find('table', {"class" : "b3k-data"}).find('tbody').find_all('tr')):
			# if planpunkt.find('a', title='Lehrveranstaltungsanmeldung'):
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

		# Select first element in Select Options Dropdown if not provided
		self.args.sectionpoint = self.args.sectionpoint or form.find_control(form.controls[0].name).get_items()[0].name
		item = form.find_control(form.controls[0].name).get(self.args.sectionpoint)
		logger.info("sectionpoint: %s" % item.name)
		item.selected = True

		timeserver = "timeserver.wu.ac.at"
		logger.info("syncing time with %s" % timeserver)

		# timeserver sync
		c = ntplib.NTPClient()
		response = c.request(timeserver, version=3)
		logger.info("time difference: %.10f (difference is taken into account)" % response.offset)
		offset = response.offset  # seconds before start time when the request should be made
		logger.info("offset: %.2f" % offset)

		# Build list of pairs (backward compatible)
		pairs = getattr(self.args, 'pairs', []) or []
		if not pairs and self.args.planobject and self.args.course:
			pairs = [(self.args.planobject, self.args.course)]

		if not pairs:
			logger.opt(colors=True).error('<red>No PP–LV pairs provided. Use -pp/-lv (repeatable) or --pair PP:LV.</red>')
			return

		# optional deprecated fallback for secondary LV (applies to all pairs if given)
		deprecated_lv2 = getattr(self.args, 'course2', None)

		self.data = {}

		def _open_pp_page(pp_raw):
			"""Open study overview, navigate to PP page, return soup of PP page and PP anchor id."""
			pp = "S" + str(pp_raw)
			self.browser.select_form('ea_stupl')
			form = self.browser.form
			item = form.find_control(form.controls[0].name).get(self.args.sectionpoint)
			item.selected = True
			r = self.browser.submit()
			soup_overview = BeautifulSoup(r.read(), "html.parser")
			link_cell = soup_overview.find('table', {"class": "b3k-data"}).find('a', id=pp)
			if not link_cell:
				logger.opt(colors=True).error(f"<red>Planpunkt {pp_raw} not found on the page (id {pp}).</red>")
				return None, None
			url = link_cell.parent.findAll('a', href=True, title="Lehrveranstaltungsanmeldung")[0]["href"]
			r = self.browser.open(self.URL_scraped + url)
			return BeautifulSoup(r.read(), "html.parser"), pp

		def _prepare_for_pair(pp_raw, lv, lv2=None):
			"""Open PP page, validate LV(s), and build mechanize request objects for primary and optional secondary."""
			soup_pp, _ = _open_pp_page(pp_raw)
			if soup_pp is None:
				return None
			lv_exists = bool(soup_pp.find('table', {"class": "b3k-data"}).find('a', text=str(lv)))
			lv2_exists = bool(soup_pp.find('table', {"class": "b3k-data"}).find('a', text=str(lv2))) if lv2 else True
			if not (lv_exists and lv2_exists):
				logger.opt(colors=True).error(f"<red>lv {lv} or {lv2} not found</red>")
				logger.opt(colors=True).info("<yellow>check if the course is available in lpis</yellow>")
				return None
			form1 = soup_pp.find('table', {"class": "b3k-data"}).find('a', text=str(lv)).parent.parent.select('.action form')[0]["name"].strip()
			form2 = None
			if lv2:
				f2 = soup_pp.find('table', {"class": "b3k-data"}).find('a', text=str(lv2))
				if f2:
					form2 = f2.parent.parent.select('.action form')[0]["name"].strip()
			# build mechanize Request objects
			self.browser.select_form(form1)
			req1 = None
			if not form1.startswith("WLDEL"):
				req1 = self.browser.click()
			req2 = None
			if lv2 and form2 and not form2.startswith("WLDEL"):
				self.browser.select_form(form2)
				req2 = self.browser.click()
			return {
				"pp": pp_raw,
				"lv": lv,
				"lv2": lv2,
				"form1": form1,
				"form2": form2,
				"req1": req1,
				"req2": req2
			}

		def _submit_and_parse(_request, _form, _lv):
			logger.info("submitting registration form (%s)" % (_request.get_full_url() if hasattr(_request, 'get_full_url') else 'request'))
			_request.set_data(_request.get_data().replace("&DISABLED=DISABLED", ""))
			_params = dict(pair.split("=", 1) if "=" in pair else (pair, "") for pair in _request.get_data().split("&"))
			_data = {
				"SH": self.args.sectionpoint.split("_")[-1],
				"T": _form.split("_")[-2],
				"LV": _form.split("_")[-2],
				"VID": _lv,
				"RA": "span",
				"cmd": "anmelden"
			}
			for (k, v) in _data.items():
				if _params.get(k):
					if k == "cmd" and _params[k] == "eintragen":
						continue
					if k == "RA" and _params[k] == "wladd":
						continue
				_params[k] = v
			_request.set_data("&".join(f"{k}={v}" for k, v in _params.items()))
			starttime = time.time_ns()
			logger.opt(colors=True).info("<green>start request %s</green>" % datetime.datetime.now())
			logger.opt(colors=True).info("fetching: %s %s %s" % (_request.get_method(), _request.get_full_url(), _request.get_data()))
			_response = self.browser.open(_request)
			logger.opt(colors=True).info("<green>end request %s</green>" % datetime.datetime.now())
			logger.info(f"request time {(time.time_ns() - starttime) / 1000000000}s")
			return BeautifulSoup(_response.read(), "html.parser")

		# 1) Determine trigger time from FIRST pair only
		first_pp, first_lv = pairs[0]
		soup_first, _ = _open_pp_page(first_pp)
		if soup_first is None:
			return
		date = soup_first.find('table', {"class": "b3k-data"}).find('a', text=str(first_lv)).parent.parent.select('.action .timestamp span')[0].text.strip()
		triggertime = 0
		if 'ab' in date:
			triggertime = time.mktime(datetime.datetime.strptime(date[3:], "%d.%m.%Y %H:%M").timetuple()) - offset

		logger.info("triggertime (from first pair): %s" % triggertime)

		# If far away, wait until 5 minutes before, then re-login once
		if (triggertime - time.time()) > 600:
			logger.opt(colors=True).info("<yellow>registration starts in more than 10 minutes</yellow>")
			logger.opt(colors=True).info("<green>waiting until 5 minutes before the registration starts</green>")
			login_triggertime = triggertime - 300
			while time.time() < login_triggertime:
				remaining_time = login_triggertime - time.time()
				hours, remainder = divmod(remaining_time, 3600)
				minutes, seconds = divmod(remainder, 60)
				print("logging in again in: {:02d}:{:02d}:{:04.1f}".format(int(hours), int(minutes), seconds), end="\r")
				time.sleep(0.1)
			self.login()
			# re-select sectionpoint after login
			self.browser.select_form('ea_stupl')
			form = self.browser.form
			item = form.find_control(form.controls[0].name).get(self.args.sectionpoint)
			item.selected = True

		# 2) Within last 5 minutes: PREPARE ALL PAIRS (requests/forms)
		prepared = []
		for (pp_raw, lv) in pairs:
			prep = _prepare_for_pair(pp_raw, lv, deprecated_lv2)
			if prep is not None:
				prepared.append(prep)

		if not prepared:
			logger.opt(colors=True).error('<red>No valid pairs could be prepared.</red>')
			return

		# Wait until exact trigger time (if still in the future)
		if triggertime > time.time():
			logger.info("waiting until: %s (%ss)" % (time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(triggertime)), triggertime))
			while time.time() < triggertime:
				remaining_time = triggertime - time.time()
				hours, remainder = divmod(remaining_time, 3600)
				minutes, seconds = divmod(remainder, 60)
				print("starting in: {:02d}:{:02d}:{:05.2f}".format(int(hours), int(minutes), seconds), end="\r")

		logger.info("final open time start: %s" % datetime.datetime.now())
		logger.opt(colors=True).info("<green>registration window assumed open — sending POST requests for all pairs</green>")

		# 3) FIRE ALL Pairs sequentially at triggertime
		for entry in prepared:
			pp_raw = entry["pp"]
			lv = entry["lv"]
			lv2 = entry["lv2"]
			form1 = entry["form1"]
			form2 = entry["form2"]
			req1 = entry["req1"]
			req2 = entry["req2"]
			logger.opt(colors=True).info(f"<cyan>Submitting</cyan> PP={pp_raw} LV={lv}{' (LV2=' + str(lv2) + ')' if lv2 else ''}")

			soup_after = _submit_and_parse(req1, form1, lv) if req1 else None
			alert_content = soup_after.find('div', {"class": 'b3k_alert_content'}) if soup_after else None
			if alert_content:
				logger.opt(colors=True).info("<bold>" + alert_content.text.strip() + "</bold>")

			transaction_error = soup_after.find("h3") if soup_after else None
			if transaction_error and "Transaktionsreihenfolge" in transaction_error.find('span').text.strip():
				logger.opt(colors=True).info("<yellow>%s</yellow>" % transaction_error.find('span').text.strip())
				logger.opt(colors=True).info("<yellow>error — retrying immediately</yellow>")
				# retry once immediately on the primary
				soup_after = _submit_and_parse(req1, form1, lv) if req1 else soup_after
				alert_content = soup_after.find('div', {"class": 'b3k_alert_content'}) if soup_after else None
				if alert_content:
					logger.opt(colors=True).info("<bold>" + alert_content.text.strip() + "</bold>")

			# secondary attempt if primary failed (or waitlist) and lv2 available
			if req2 is not None:
				neg_primary = bool(alert_content and ("nicht" in alert_content.text.strip()))
				on_waitlist = bool(alert_content and ("Warteliste" in alert_content.text.strip()))
				if neg_primary or on_waitlist:
					logger.opt(colors=True).info("<yellow>trying secondary LV</yellow>")
					_soup2 = _submit_and_parse(req2, form2, lv2)
					ac2 = _soup2.find('div', {"class": 'b3k_alert_content'})
					if ac2:
						logger.opt(colors=True).info("<bold>" + ac2.text.strip() + "</bold>")
