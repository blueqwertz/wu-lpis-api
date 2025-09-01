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
		# Select first element in Select Options Dropdown
		self.args.sectionpoint = self.args.sectionpoint or form.find_control(form.controls[0].name).get_items()[0].name
		print(self.args.sectionpoint)
		item = form.find_control(form.controls[0].name).get(self.args.sectionpoint)
		logger.info("sectionpoint: %s" % item.name)
		item.selected = True
		
		timeserver = "timeserver.wu.ac.at"
		logger.info("syncing time with %s" % timeserver)

		# timeserver sync
		c = ntplib.NTPClient()
		response = c.request(timeserver, version=3)
		logger.info("time difference: %.10f (difference is taken into account)" % response.offset)

		offset = 0.5 + response.offset	# seconds before start time when the request should be made
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

		# check if lv and lv2 exist
		if not soup.find('table', {"class" : "b3k-data"}).find('a', text=lv) or not soup.find('table', {"class" : "b3k-data"}).find('a', text=lv2):
			logger.opt(colors=True).error("<red>lv %s or %s not found</red>" % (lv, lv2))
			logger.opt(colors=True).info("<yellow>check if the course is available in lpis</yellow>")
			return

		# wait until registration start time - offset
		date = soup.find('table', {"class" : "b3k-data"}).find('a', text=lv).parent.parent.select('.action .timestamp span')[0].text.strip()
		if 'ab' in date and False:
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

		print("ENTERING CRITICAL TIME ZONE — NO MORE DELAYS")

		logger.info("triggertime: %s" % triggertime)
		logger.info("final open time start: %s" % datetime.datetime.now())
		
		# prepare form submission requests in advance and fire POST directly at trigger time
		logger.opt(colors=True).info("<green>preparing prebuilt POST requests</green>")

		# extract the form names for the primary and (optional) secondary LV
		form1 = soup.find('table', {"class": "b3k-data"}).find('a', text=lv).parent.parent.select('.action form')[0]["name"].strip()
		form2 = soup.find('table', {"class": "b3k-data"}).find('a', text=lv2).parent.parent.select('.action form')[0]["name"].strip() if lv2 else None

		# build raw mechanize Request objects ahead of time
		self.browser.select_form(form1)
		req1 = self.browser.click()  # ready-to-send POST for primary LV

		req2 = None
		if lv2 and form2 and not form2.startswith("WLDEL"):
			self.browser.select_form(form2)
			req2 = self.browser.click()  # ready-to-send POST for secondary LV

		logger.opt(colors=True).info("<green>registration window assumed open — sending POST request</green>")

		while True:
			# fire the primary POST request immediately without reloading
			def _submit_and_parse(_request, _form, _lv):
				logger.info("submitting registration form (%s)" % (_request.get_full_url() if hasattr(_request, 'get_full_url') else 'request'))
				_request.set_data(_request.get_data().replace("&DISABLED=DISABLED", ""))

				_data = {
					"SH": self.args.sectionpoint.split("_")[-1],
					"T": _form.split("_")[-2],
					"LV": _form.split("_")[-2],
					"VID": _lv,
					"RA": "span"
				}
				
				for (_k, _v) in _data.items():
					if not _k in str(_request.get_data()):
						_request.set_data(_request.get_data() + "&%s=%s" % (_k, _v))

				# print url and all data of the request for debugging
				print("DEBUG",_request.get_full_url(),_request.get_data())

				starttime = time.time_ns()
				resp = self.browser.open(_request)
				logger.opt(colors=True).info("<green>end request %s</green>" % datetime.datetime.now())
				logger.info(f"request time {(time.time_ns() - starttime) / 1000000000}s")
				return BeautifulSoup(resp.read(), "html.parser")

			soup = _submit_and_parse(req1, form1, lv)

			# handle server feedback; if not successful and lv2 exists, try the secondary POST immediately
			alert_content = soup.find('div', {"class": 'b3k_alert_content'})
			if alert_content:
				logger.opt(colors=True).info("<bold>" + alert_content.text.strip() + "</bold>")

			# if there's a transaction error, retry the primary submission immediately
			transaction_error = soup.find("h3")
			if transaction_error and "Transaktionsreihenfolge" in transaction_error.find('span').text.strip():
				logger.opt(colors=True).info("<yellow>%s</yellow>" % transaction_error.find('span').text.strip())
				logger.opt(colors=True).info("<yellow>too early — retrying immediately</yellow>")
				continue

			# consider it a failure if there's an alert with a negative message not related to waitlist
			if bool(alert_content and ("nicht" in alert_content.text.strip()) and ("Warteliste" not in alert_content.text.strip())) and req2 is not None:
				logger.opt(colors=True).info("<yellow>primary submission failed — trying secondary LV immediately</yellow>")
				soup = _submit_and_parse(req2, form2, lv2)
				alert_content = soup.find('div', {"class": 'b3k_alert_content'})
				if alert_content:
					logger.opt(colors=True).info("<bold>" + alert_content.text.strip() + "</bold>")			
			break
