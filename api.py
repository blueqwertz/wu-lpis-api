try:
	import argparse
	import os
	import traceback
	from WuLpisApiClass import WuLpisApi
	from ms_login import MicrosoftLoginError
	from logger import logger, set_user_name, set_action
	import updater
	import users
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
	# kept for compatibility - the parser itself now lives next to the user
	# database, so both read a credentials file the same way
	return users.read_credentials_file(filepath, separator)

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
	parser.add_argument('-U', '--user', help="Name of a stored user profile (see --users)")
	parser.add_argument('--users', action='store_true', help="Manage the stored user profiles and exit")
	parser.add_argument('--list-users', action='store_true', dest='list_users', help="List the stored user profiles and exit")
	parser.add_argument('--save-user', action='store_true', dest='save_user', help="Store the used settings as a user profile")
	parser.add_argument('--save-password', action='store_true', dest='save_password', help="With --save-user: also store the password (plain text)")
	args=parser.parse_args()

	# opening the store also takes over any old .credentials file exactly once,
	# so an existing setup keeps working without the user doing anything
	store = users.UserStore()
	for note in store.notes:
		logger.info(note)
	store.notes = []

	# the two management modes do not need a login at all
	if args.users:
		users.manage(store)
		exit(0)
	if args.list_users:
		users.print_users(store)
		exit(0)

	# a credentials file is only read when it was asked for explicitly - that
	# keeps existing cron jobs working. The file the tool used to read by
	# itself now lives in the user database, otherwise a hand edit there would
	# be silently overruled by the old file
	credentials = {}
	explicit_credfile = args.credfile != parser.get_default('credfile')
	if explicit_credfile:
		if not os.path.isfile(args.credfile):
			parser.error("credentials file '%s' not found" % args.credfile)
		credentials = users.read_credentials_file(args.credfile)
	elif not len(store) and args.credfile and os.path.isfile(args.credfile):
		# the database could not be written (read only directory?) - fall back
		# to the file instead of leaving the user without credentials
		credentials = users.read_credentials_file(args.credfile)

	# a stored profile fills in whatever was not given on the command line.
	# --user picks one explicitly, otherwise the default profile jumps in when
	# nothing else supplies a username
	profile = None
	if args.user:
		profile = store.get(args.user)
		if profile is None:
			parser.error("no user profile '%s' - known profiles: %s"
						 % (args.user, ", ".join(store.names()) or "none"))
	elif not args.username and not credentials.get("username"):
		profile = store.default_user()

	if profile is None:
		profile = users.User()

	def setting(name):
		"""command line > profile > credentials file"""
		return getattr(args, name, None) or profile.get(name) or credentials.get(name)

	username = setting("username")
	password = setting("password")

	if not username:
		parser.error("no username given (--username, --user or credentials file)")

	if args.msdomain == parser.get_default("msdomain"):
		args.msdomain = profile.get("msdomain") or credentials.get("msdomain") or args.msdomain
	args.mfa_method = setting("mfa_method")
	args.sessiondir = setting("sessiondir")
	if args.sessiondir and not args.sessiondir.endswith(("/", "\\", os.sep)):
		# the username is appended directly, so the separator has to be there
		args.sessiondir += os.sep
	args.planobject = setting("planobject")
	args.course = setting("course")
	args.course2 = setting("course2")
	if args.offset == parser.get_default("offset") and profile.get("offset"):
		args.offset = float(profile["offset"])

	logger.add("logs/output-%s.log" % username, level="INFO", colorize=False)
	set_user_name(username)
	set_action(args.action)

	args.sectionpoint = setting("sectionpoint")

	if args.save_user:
		profile["username"] = username
		profile["name"] = args.user or profile.name or username
		for name in ("msdomain", "mfa_method", "sessiondir", "sectionpoint",
					 "planobject", "course", "course2"):
			profile[name] = getattr(args, name, None) or ""
		profile["offset"] = args.offset
		profile["password"] = password if args.save_password else ""
		store.put(profile)
		store.save()
		logger.info("user profile %s saved to %s" % (profile.title, store.path))

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