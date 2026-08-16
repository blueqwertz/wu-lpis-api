try:
	import argparse
	import os
	import traceback
	from WuLpisApiClass import WuLpisApi
	from ms_login import MicrosoftLoginError
	from logger import logger, set_user_name, set_action
	import updater
except ImportError as e:
	# install the missing modules
	print("Some modules are missing. Installing them now...")
	import subprocess, sys, os
	subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
	print("Restarting program...")
	os.execv(sys.executable, [sys.executable] + sys.argv)

try:
	updater.check()
except Exception:
	logger.opt(colors=True).error("<red>failed to check for updates: %s</red>" % traceback.format_exc())


def file_parser(filepath, separator="="):
	"""Read a 'key=value' credentials file.

	Blank lines and '#' comments are skipped, keys and values are stripped and
	a byte order mark is dropped. An editor that leaves a trailing space behind
	the password, or a BOM in front of the first key, otherwise breaks the
	login with nothing but "wrong username or password" (AADSTS50126).
	"""
	data = {}
	for number, raw in enumerate(open(filepath, "r", encoding="utf-8-sig"), start=1):
		line = raw.strip()
		if not line or line.startswith("#"):
			continue
		if separator not in line:
			raise ValueError("%s line %d: expected 'key%svalue', got %r"
							 % (filepath, number, separator, line))
		key, value = line.split(separator, 1)
		data[key.strip()] = value.strip()
	return data

if __name__ == '__main__':
	parser=argparse.ArgumentParser()
	parser.add_argument('-a', '--action', help="Which action in the programm should run", default="infos")
	parser.add_argument('-c', '--credfile', help='Path to the credentials file with username and password', default=".credentials")
	parser.add_argument('-u', '--username')
	parser.add_argument('-p', '--password')
	parser.add_argument('-s', '--sessiondir', help='Dir where the sessions should be stored')
	parser.add_argument('-sp', '--sectionpoint', help='Study section in which the planobject can be found (Studium/Abschnitt)')
	parser.add_argument('-pp', '--planobject', help="Study plan object in which the correspondending course can be found (Studienplanpunkt")
	parser.add_argument('-lv', '--course', help="Course ID for which the registration should be done")
	parser.add_argument('-lv2', '--course2', help="Fallback (second) Course ID")
	parser.add_argument('-o', '--offset', help="Offset in seconds for the time of registration", type=float, default=0.7)
	parser.add_argument('-d', '--msdomain', help="Domain appended to the username for the Microsoft login", default="s.wu.ac.at")
	parser.add_argument('-m', '--mfa-method', dest='mfa_method', help="Microsoft 2FA method (e.g. PhoneAppNotification, PhoneAppOTP, OneWaySMS)")
	args=parser.parse_args()

	# the credentials file is optional: it is only read when it exists, and
	# values given on the command line always win
	credentials = {}
	if args.credfile and os.path.isfile(args.credfile):
		try:
			credentials = file_parser(args.credfile)
		except ValueError as error:
			parser.error(str(error))
	elif args.credfile and not (args.username or args.password):
		parser.error("credentials file '%s' not found - "
					 "provide it or use --username/--password" % args.credfile)

	username = args.username or credentials.get("username")
	password = args.password or credentials.get("password")

	if not username:
		parser.error("no username given (--username or credentials file)")

	if "msdomain" in credentials and args.msdomain == parser.get_default("msdomain"):
		args.msdomain = credentials["msdomain"]
	if "mfa_method" in credentials and not args.mfa_method:
		args.mfa_method = credentials["mfa_method"]

	logger.add("logs/output-%s.log" % username, level="INFO", colorize=False)
	set_user_name(username)
	set_action(args.action)

	if "sectionpoint" in credentials and not args.sectionpoint:
		args.sectionpoint = credentials["sectionpoint"]
	try:
		api = WuLpisApi(username, password, args, args.sessiondir)
		method = getattr(api, args.action, None)
		if callable(method):
			method()
		else:
			logger.log("This action is not available.")
	except MicrosoftLoginError as error:
		# an expected outcome (denied 2FA, wrong password, ...) - report it
		# plainly instead of dumping a traceback at the user
		# "\<" keeps loguru from reading a stray angle bracket as colour markup
		logger.opt(colors=True).error(
			"<red>login failed: %s</red>" % str(error).replace("<", "\\<"))
		exit(1)
	except Exception:
		logger.error(traceback.format_exc())
		exit()