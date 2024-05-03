#!/usr/bin/env python
# -*- coding: utf-8 -*-

import datetime, re, os, time, pickle
from lxml import html
from bs4 import BeautifulSoup

import mechanize, time
import ntplib

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
		print ("init time: %s" % starttime)
		self.data = {}

		#if not self.load_session():
		print("logging in %s..." % self.username)

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

		print(f"request time {(time.time_ns() - starttime) / 1000000000}s")

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
		# print "trying to save session ..."
		if not os.path.exists(os.path.dirname(self.sessionfile)):
			try:
				os.makedirs(os.path.dirname(self.sessionfile))
			except:
				if exc.errno != errno.EEXIST:
					raise
		with open(self.sessionfile, 'wb') as file:
			try:
				# dill.dump(self.browser, file)
				pickle.dump(self.browser, file, pickle.HIGHEST_PROTOCOL)
			except:
				return False
		# print "session saved to file ..."
		return True


	def load_session(self):
		# print "trying to load session ..."
		if os.path.isfile(self.sessionfile):
			with open(self.sessionfile, 'rb') as file:
				try:
					self.browser = pickle.load(file)
				except:
					return False
			# print "session loaded from file ..."
			return True


	def infos(self):
		# print "getting data ..."
		self.data = {}
		self.browser.select_form('ea_stupl')
		
		form = self.browser.form
		# Select first element in Select Options Dropdown
		item = form.find_control(form.controls[0].name).get(None ,None, None, 0)
		item.selected = True

		r = self.browser.submit()
		
		self.browser.select_form('ea_stupl')
		form = self.browser.form

		soup = BeautifulSoup(r.read(), "html.parser")

		# studies = {}
		# for i, entry in enumerate(soup.find('select', {'name': form.controls[0].name}).find_all('option')):
		# 	# print(i, len(entry.text.split("/")), (i-1) % max(len(studies), 1), len(studies), entry.text.split("/"))
		# 	print(entry)
		# 	# if len(entry.text.split('/')) == 1:
		# 	# 	studies[i] = {}
		# 	# 	studies[i]['id'] = entry['value']
		# 	# 	studies[i]['title'] = entry['title']
		# 	# 	studies[i]['name'] = entry.text
		# 	# 	studies[i]['abschnitte'] = {}
		# 	# elif len(entry.text.split('/')) == 2 and entry.text.split('/')[0] == studies[(i-1) % len(studies)]['name']:
		# 	# 	studies[(i-1) % len(studies)]['abschnitte'][entry['value']] = {}
		# 	# 	studies[(i-1) % len(studies)]['abschnitte'][entry['value']]['id'] = entry['value']
		# 	# 	studies[(i-1) % len(studies)]['abschnitte'][entry['value']]['title'] = entry['title']
		# 	# 	studies[(i-1) % len(studies)]['abschnitte'][entry['value']]['name'] = entry.text

		# # self.data['studies'] = studies

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
				if planpunkt.select('a[href*="GP"]'):
					pp[key]["prf_url"] = planpunkt.select('a[href*="GP"]')[0]['href']

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

		lv_index = 0

		lv_register = []

		for pp_id in pp:
			print(f"{'   ' * int(pp[pp_id]['depth'])}{pp_id} {pp[pp_id]['name']}")
			if "lvs" in pp[pp_id] and "" in pp[pp_id]["lvs"]:
				print(f"\033[94m{'   ' * int(pp[pp_id]['depth'] + 1)}{pp[pp_id]['lv_status']}\033[0m")
			elif "lvs" in pp[pp_id]:
				for lv_id in pp[pp_id]["lvs"]:
					lv_index += 1
					lv = pp[pp_id]["lvs"][lv_id]
					lv_register.append({"lv": lv_id, "pp": pp_id, "name": pp[pp_id]["name"]})
					print(f"{'   ' * int(pp[pp_id]['depth'] + 1)}", end="")

					print("\033[91m" if int(lv["free"]) == 0 or lv["status"] == "Anmeldung nicht möglich" else "\033[92m", end="")
					if "date_start" in lv:
						print("\033[93m", end="")

					print("[{:03d}] {:<3} {:<4} - {:<9} {:<25} {:>4}/{:<4} {:<27}".format(lv_index, pp[pp_id]["type"], lv["id"], lv["semester"], lv["prof"][0:25], lv["free"], lv["capacity"], lv["status"]), end="")

					print(f"(Anmeldung ab: {lv['date_start']})" if "date_start" in lv else "", end="")
					print(f"(Anmeldung bis: {lv['date_end']})" if "date_end" in lv else "", end="")
					
					print("\033[0m")

		if input("register for a course [y/N]: ").lower() == "y":
			register_id = int(input("enter course index: "))
			if input("do you want to register for %s (LV: %s) [y/N]: " % (lv_register[register_id - 1]["name"],lv_register[register_id - 1]["lv"])).lower() == "y":
				self.args.planobject = lv_register[register_id - 1]["pp"]
				self.args.course = lv_register[register_id - 1]["lv"]

				self.registration()
							
		self.data['pp'] = pp				
		return self.data


	def registration(self):

		self.browser.select_form('ea_stupl')
		
		form = self.browser.form
		# Select first element in Select Options Dropdown
		item = form.find_control(form.controls[0].name).get(None ,None, None, 0)
		item.selected = True
		
		# timeserver = "timeserver.wu.ac.at"
		# print("syncing time with \"%s\"" % timeserver)

		# # timeserver sync
		# c = ntplib.NTPClient()
		# response = c.request('timeserver.wu.ac.at', version=3)
		# print ("time difference: %.10f (difference is taken into account)" % response.offset)

		offset = 0.8	# seconds before start time when the request should be made
		print("offset: %s" % offset)
		if self.args.planobject and self.args.course:
			pp = "S" + self.args.planobject
			lv = self.args.course
			lv2 = self.args.course2 or lv
		
		self.data = {}
		self.browser.select_form('ea_stupl')
		r = self.browser.submit()
		soup = BeautifulSoup(r.read(), "html.parser")

		url = soup.find('table', {"class" : "b3k-data"}).find('a', id=pp).parent.find('a', href=True)["href"]
		r = self.browser.open(self.URL_scraped + url)

		triggertime = 0
		soup = BeautifulSoup(r.read(), "html.parser")
		date = soup.find('table', {"class" : "b3k-data"}).find('a', text=lv).parent.parent.select('.action .timestamp span')[0].text.strip()
		if 'ab' in date:
			triggertime = time.mktime(datetime.datetime.strptime(date[3:], "%d.%m.%Y %H:%M").timetuple()) - offset

			if triggertime > time.time():
				print("waiting until: %s (%ss)" % (time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(triggertime)), triggertime))
				# while time.time() < triggertime:
				# 	remaining_time = triggertime - time.time()
				# 	hours, remainder = divmod(remaining_time, 3600)
				# 	minutes, seconds = divmod(remainder, 60)
				# 	print("starting in: {:02d}:{:02d}:{:05.2f}".format(int(hours), int(minutes), seconds), end="\r")				
				# time.sleep( triggertime - time.time() )

		print("triggertime: %s" % triggertime)
		print("final open time start: %s" % datetime.datetime.now())
		
		# Submit registration until it was successful
		while True:
	
			# Reload page until registration is possible
			while True:
				starttime = time.time_ns()
				print("start request %s" % datetime.datetime.now())
				r = self.browser.open(self.URL_scraped + url)
				print("end request %s" % datetime.datetime.now())
				print(f"request time {(time.time_ns() - starttime) / 1000000000}s")
				soup = BeautifulSoup(r.read(), "html.parser")
				if soup.find('table', {"class" : "b3k-data"}).find('a', text=lv).parent.parent.select('div.box.possible'):
					# break out of loop to start registration progress
					break
				else:
					print("parsing done %s" % datetime.datetime.now())
				print("registration is not (yet) possibe, waiting ...")
				print("reloading page and waiting for form to be submittable")

			print("final open time end: %s" % datetime.datetime.now())
			print("registration is possible")

			cap1 = soup.find('table', {"class" : "b3k-data"}).find('a', text=lv).parent.parent.select('div[class*="capacity_entry"]')[0].text.strip()
			cap2 = soup.find('table', {"class" : "b3k-data"}).find('a', text=lv2).parent.parent.select('div[class*="capacity_entry"]')[0].text.strip()
			free1 = int(cap1[:cap1.rindex('/')-1])
			free2 = int(cap2[:cap2.rindex('/')-1])

			form1 = soup.find('table', {"class" : "b3k-data"}).find('a', text=lv).parent.parent.select('.action form')[0]["name"].strip()
			form2 = soup.find('table', {"class" : "b3k-data"}).find('a', text=lv2).parent.parent.select('.action form')[0]["name"].strip()

			print("end time: %s" % datetime.datetime.now())
			print("freie plaetze: lv1: %s, lv2: %s (if defined)" % (free1, free2))
			if free1 > 0:
				if not form1.startswith("WLDEL"):
					self.browser.select_form(form1)
					print("submitting registration form1 (%s)" % form1)
				else:
					print("skipping form1 (%s)" % form1)
			elif lv2:
				if not form2.startswith("WLDEL"):
					self.browser.select_form(form2)
					print("submitting registration form2 (%s)" % form2)
				else:
					print("skipping form2 (%s)" % form2)

			r = self.browser.submit()

			soup = BeautifulSoup(r.read(), "html.parser")

			alert_content = soup.find('div', {"class" : 'b3k_alert_content'})
			
			# Check if alert_content is available + check if registration failed
			if alert_content and "nicht" in alert_content.text.strip() and "Warteliste" not in alert_content.text.strip():
				print('\033[91m%s\033[0m' % alert_content.text.strip())
				continue
			
			if alert_content:
				alert_text = alert_content.text.strip()
				print(alert_text)
				lv = soup.find('table', {"class" : "b3k-data"}).find('a', text=lv).parent.parent
				print("Frei: " + lv.select('div[class*="capacity_entry"]')[0].text.strip())
				wl_title = "Anzahl Warteliste" if not "Warteliste" in alert_text else "aktuelle Wartelistenposition / Anzahl Wartelisteneinträge"
				if lv.select('td.capacity div[title*="%s"]' % wl_title):
					print("Warteliste: " + lv.select('td.capacity div[title*="%s"] span' % wl_title)[0].text.strip() + " / " + lv.select('td.capacity div[title*="%s"] span' % wl_title)[0].text.strip())
					if free1 > 0:
						try:
							if not form2.startswith("WLDEL"):
								self.browser.select_form(form2)
								print("submitting registration form2 (%s)" % form)
								r = self.browser.submit()
							else:
								print("skipping form2 (%s)" % form2)
						except:
							print("could not submit form (%s)" % form2)

			if soup.find('h3'):
				print(soup.find('h3').find('span').text.strip())

			break

