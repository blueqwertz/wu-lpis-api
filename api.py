try:
	import argparse
	import math
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
	data = {}
	for line in open(filepath, "r"):
		line = line.rstrip('\n').split(separator, 1)
		data[line[0]] = line[1]
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
	parser.add_argument('-o', '--offset', help="No longer used: the burst covers the whole opening window instead of firing at a single point", type=float, default=None)
	parser.add_argument('--burst-lead', help="Seconds before the opening second at which the burst starts (default 3)", type=float, default=3.0)
	parser.add_argument('--burst-interval', help="Seconds between two burst requests (default 0.1)", type=float, default=0.1)
	parser.add_argument('--burst-deadline', help="Seconds after the opening second after which the burst gives up (default 10)", type=float, default=10.0)
	parser.add_argument('--burst-workers', help="Number of parallel connections used for the burst (default: auto, enough to keep the whole lead-in in flight)", type=int, default=0)
	parser.add_argument('-d', '--msdomain', help="Domain appended to the username for the Microsoft login", default="s.wu.ac.at")
	parser.add_argument('-m', '--mfa-method', dest='mfa_method', help="Microsoft 2FA method (e.g. PhoneAppNotification, PhoneAppOTP, OneWaySMS)")
	args=parser.parse_args()

	# --offset is kept so existing scripts and cron entries keep running, but
	# it no longer steers anything; say so instead of silently ignoring it
	args.offset_explicit = args.offset is not None
	if args.offset is None:
		args.offset = 0.0

	if args.burst_interval <= 0:
		parser.error("--burst-interval must be greater than 0")
	if args.burst_workers < 0:
		parser.error("--burst-workers cannot be negative")

	# the credentials file is optional: it is only read when it exists, and
	# values given on the command line always win
	credentials = {}
	if args.credfile and os.path.isfile(args.credfile):
		credentials = file_parser(args.credfile)
	elif args.credfile and not (args.username or args.password):
		parser.error("credentials file '%s' not found - "
					 "provide it or use --username/--password" % args.credfile)

	username = args.username or credentials.get("username")
	password = args.password or credentials.get("password")

	if not username:
		parser.error("no username given (--username or credentials file)")

	# without this a typo in the credentials file (a misspelled key, say) just
	# leaves the password empty and the run dies somewhere inside the Microsoft
	# login instead of here, where the cause is obvious
	if not password:
		parser.error("no password given (--password or credentials file)%s"
					 % (" - keys found in %s: %s" % (args.credfile, ", ".join(credentials))
						if credentials else ""))

	if "msdomain" in credentials and args.msdomain == parser.get_default("msdomain"):
		args.msdomain = credentials["msdomain"]
	if "mfa_method" in credentials and not args.mfa_method:
		args.mfa_method = credentials["mfa_method"]

	logger.add("logs/output-%s.log" % username, level="INFO", colorize=False)
	set_user_name(username)
	set_action(args.action)

	# Under load LPIS takes seconds to answer, so a request fired before the
	# opening second is often still being processed when it arrives - which is
	# what makes the lead-in the valuable part of the burst. That only works if
	# those requests are all in flight at once: with fewer workers than the
	# lead-in has grid points, the pool runs full and the grid stops being
	# fired, so everything from that point up to the opening second is lost.
	needed = math.ceil(args.burst_lead / args.burst_interval)
	if args.burst_workers == 0:
		args.burst_workers = max(8, min(needed, 64))
		logger.info("burst workers: %d (auto - covers the %.1fs lead-in at %.2fs)"
					% (args.burst_workers, args.burst_lead, args.burst_interval))
		if needed > args.burst_workers:
			logger.opt(colors=True).warning(
				"<yellow>the lead-in would need %d connections, capped at %d - "
				"raise --burst-interval or lower --burst-lead to keep the whole "
				"grid in flight</yellow>" % (needed, args.burst_workers))
	elif args.burst_workers < needed:
		logger.opt(colors=True).warning(
			"<yellow>--burst-workers %d is below the %d the lead-in needs: the "
			"burst stalls once every worker waits on an answer, and the grid "
			"from then until the opening second never goes out</yellow>"
			% (args.burst_workers, needed))

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