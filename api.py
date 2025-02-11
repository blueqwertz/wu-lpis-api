import argparse, json
from WuLpisApiClass import WuLpisApi

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
	parser.add_argument('-pp', '--planobject', help="Study plan object in which the correspondending course can be found (Studienplanpunkt")
	parser.add_argument('-lv', '--course', help="Course ID for which the registration should be done")
	parser.add_argument('-lv2', '--course2', help="Fallback (second) Course ID")
	args=parser.parse_args()

	username = file_parser(args.credfile)["username"] if args.credfile else args.username
	password = file_parser(args.credfile)["password"] if args.credfile else args.password
	
	if args.credfile and "sectionpoint" in file_parser(args.credfile):
		args.sectionpoint = file_parser(args.credfile)["sectionpoint"]
	
	api = WuLpisApi(username, password, args, args.sessiondir)
	method = getattr(api, args.action, None)
	if callable(method):
		method()
		# print(json.dumps(api.getResults(), sort_keys=True, indent=4))
	else:
		print("This action is not available.")