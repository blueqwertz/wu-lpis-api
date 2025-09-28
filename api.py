import argparse
import traceback
from WuLpisApiClass import WuLpisApi
from logger import logger
import updater


try:
	updater.check()
except Exception:
	logger.opt(colors=True).error("<red>failed to check for updates: %s</red>" % traceback.format_exc())


def file_parser(filepath, separator="="):
	data = {}
	for line in open(filepath, "r"):
		line = line.rstrip('\n').split(separator)
		data[line[0]] = line[1]
	return data

if __name__ == '__main__':
	parser=argparse.ArgumentParser()
	parser.add_argument('-a', '--action', help="Which action in the programm should run", default="infos")
	parser.add_argument('-c', '--credfile', help='Path to the credentials file with username and password', default=".credentials")
	parser.add_argument('-p', '--password')
	parser.add_argument('-u', '--username')
	parser.add_argument('-s', '--sessiondir', help='Dir where the sessions should be stored')
	parser.add_argument('-sp', '--sectionpoint', help='Study section inw which the planobject can be found (Studium/Abschnitt)')
	parser.add_argument('-pp', '--planobject', action='append', help="Study plan object (Studienplanpunkt). Repeatable: -pp 342886 -pp 2284", required=False)
	parser.add_argument('-lv', '--course', action='append', help="Course ID. Repeatable and order-coupled with -pp.", required=False)
	parser.add_argument('-lv2', '--course2', help="Fallback (second) Course ID (deprecated; use -pp/-lv pairs or --pair)")
	parser.add_argument('-pr', '--pair', action='append', help="Convenience: PP:LV pair, e.g. --pair 342886:1052. Repeatable.", required=False)
	args=parser.parse_args()

	# --- PP–LV pair normalization for flexible input ---------------------------------
	# Examples:
	#   python3 api.py -a registration -pp 342886 -lv 1052 -pp 2284 -lv 7777
	#   python3 api.py -a registration --pair 342886:1052 --pair 2284:7777
	#   (mixing is allowed; inputs are combined)
	#   -lv2/--course2 is deprecated (use pairs above)
	# --- Normalize PP–LV inputs into args.pairs ---------------------------------
	pairs = []

	# 2a) Pairs from --pair "PP:LV" strings
	if args.pair:
		for item in args.pair:
			if ':' not in item:
				raise SystemExit(f"Invalid --pair '{item}'. Use format PP:LV (e.g., 342886:1052)")
			pp, lv = item.split(':', 1)
			pp = pp.strip()
			lv = lv.strip()
			if not pp or not lv:
				raise SystemExit(f"Invalid --pair '{item}'. Both PP and LV must be non-empty.")
			pairs.append((pp, lv))

	# 2b) Pairs from repeated -pp/-lv; require equal lengths when either provided
	pp_list = args.planobject or []
	lv_list = args.course or []
	if pp_list or lv_list:
		if len(pp_list) != len(lv_list):
			raise SystemExit(
				f"Mismatched counts: received {len(pp_list)} '-pp' and {len(lv_list)} '-lv'.\n"
				"Provide the same number of -pp and -lv arguments in the intended order,\n"
				"or use repeated --pair PP:LV entries."
			)
		pairs.extend(zip(pp_list, lv_list))

	# Attach normalized structure for downstream code
	setattr(args, 'pairs', pairs)

	# Backward compatibility: if exactly one pair via -pp/-lv and no --pair given,
	# keep args.planobject/args.course as scalars for legacy code paths.
	if not args.pair and len(pp_list) == 1 and len(lv_list) == 1:
		args.planobject = pp_list[0]
		args.course = lv_list[0]
	# Otherwise, leave args.planobject/args.course as lists (new behavior).

	username = file_parser(args.credfile)["username"] if args.credfile else args.username
	password = file_parser(args.credfile)["password"] if args.credfile else args.password

	logger.add("logs/output-%s.log" % username, level="INFO", colorize=False)
	
	if args.credfile and "sectionpoint" in file_parser(args.credfile) and not args.sectionpoint:
		args.sectionpoint = file_parser(args.credfile)["sectionpoint"]
	try:
		api = WuLpisApi(username, password, args, args.sessiondir)
		method = getattr(api, args.action, None)
		if callable(method):
			method()
			# logger.log(json.dumps(api.getResults(), sort_keys=True, indent=4))
		else:
			logger.log("This action is not available.")
	except Exception:
		logger.error(traceback.format_exc())
		exit()