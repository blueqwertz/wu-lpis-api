#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Login for lpis.wu.ac.at via the WU single-sign-on chain.

Since 2026 LPIS no longer has its own username/password form. The chain is:

    lpis.wu.ac.at/lpis
      -> short.wu.ac.at/wu-lpis -> bach.wu.ac.at -> lpis.wu.ac.at/lpispd
      -> bach-id.wu.ac.at (Keycloak, realm "bach", client "bach-lpislg-prod")
      -> login.microsoftonline.com (Entra ID, tenant 0504f721-...)
         -> password -> MFA (Authenticator push with number matching)
         -> "stay signed in" (KMSI)
      -> back to Keycloak -> back to lpis.wu.ac.at/kdcs/<instance>/<session>/

The Keycloak login page only offers the "wu-microsoft" identity provider and
has no local password form, so the Microsoft round trip is mandatory.

All cookies are kept in one requests.Session. Persisting that cookie jar keeps
the Microsoft ESTSAUTHPERSISTENT cookie, so password and MFA are only needed
once; later runs go through the whole chain without any prompt.
"""

import html as htmllib
import json
import os
import pickle
import re
import sys
import time
import uuid
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from logger import logger

START_URL = "https://lpis.wu.ac.at/lpis"

MS_HOSTS = (
    "login.microsoftonline.com",
    "login.microsoft.com",
    "login.live.com",
)

KEYCLOAK_HOST = "bach-id.wu.ac.at"

# Keycloak takes an identity provider hint on the OIDC authorization endpoint
# and then redirects straight to that broker. That is the documented way to
# skip the login page, so the markup of the page does not have to be parsed.
IDP_HINT_PARAM = "kc_idp_hint"
IDP_HINT_VALUE = "wu-microsoft"

# Hosts a redirect found on the Keycloak page is allowed to point to. Compared
# against the parsed hostname, so lookalike domains cannot turn the fallback
# into an open redirect.
KEYCLOAK_REDIRECT_HOSTS = (KEYCLOAK_HOST,) + MS_HOSTS

_AUTH_ENDPOINT_RE = re.compile(r"/realms/[^/]+/protocol/openid-connect/auth/?$")

# Keycloak answers the authorization request with "401 WWW-Authenticate:
# Negotiate" and a body that posts itself back to /login-actions/. Only after
# that post does the flow continue to the identity provider, so this page has
# to be submitted rather than searched for a redirect. Two is plenty - the
# realm only has one Kerberos execution.
MAX_KEYCLOAK_FORMS = 2

# Fallback for the case that Keycloak ignores the hint and renders its
# "you are being redirected" page anyway. Tried in this order, the first
# candidate that passes _safe_redirect() wins.
_REDIRECT_PATTERNS = (
    # Keycloak wraps the broker url in decodeHTMLEntities('...') / ("...").
    re.compile(r"decodeHTMLEntities\(\s*'([^']+)'\s*\)"),
    re.compile(r'decodeHTMLEntities\(\s*"([^"]+)"\s*\)'),
    # location.replace("...") / window.location.assign('...')
    re.compile(r"""(?<![\w$.])(?:window\.|document\.)?location\."""
               r"""(?:replace|assign)\(\s*['"]([^'"]+)['"]\s*\)"""),
    # window.location = "..." / window.location.href = '...'
    re.compile(r"""(?<![\w$.])(?:window\.|document\.)?location(?:\.href)?"""
               r"""\s*=\s*['"]([^'"]+)['"]"""),
    # <a href=".../broker/<alias>/login?...">
    re.compile(r"""href\s*=\s*['"]([^'"]*broker/[^'"/]+/login[^'"]*)['"]""", re.I),
    # <meta http-equiv="refresh" content="0; url=...">
    re.compile(r"""<meta[^>]+http-equiv\s*=\s*['"]?refresh['"]?[^>]*"""
               r"""content\s*=\s*['"][^'"]*?url\s*=\s*([^'"]+)['"]""", re.I),
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# MFA methods where the user reads a code somewhere and types it into the
# terminal. Everything else is treated as an out-of-band approval that is
# polled until the user confirms it on the phone.
OTP_METHODS = ("PhoneAppOTP", "OneWaySMS", "ConsolidatedTelephony")

MAX_STEPS = 25
POLL_INTERVAL = 1.0

# What Microsoft reports when an approval did not simply succeed. Without this
# the user only saw the raw result value, e.g. "PhoneAppDenied".
MFA_OUTCOMES = {
    "PhoneAppDenied":
        "the sign-in was denied in the Authenticator app",
    "PhoneAppNoResponse":
        "nobody reacted to the Authenticator prompt",
    "PhoneAppFraud":
        "the sign-in was reported as fraud in the Authenticator app - Microsoft "
        "may have blocked the account, check https://mysignins.microsoft.com",
    "AuthenticationMethodLocked":
        "the 2FA method is temporarily locked by Microsoft",
    "SASValidationFailed":
        "Microsoft could not validate the 2FA response",
    "InvalidSessionId":
        "the 2FA session expired - please start the login again",
    "UserAuthFailedDuplicateRequest":
        "Microsoft is still busy with the previous prompt - wait a minute "
        "before trying again",
}

# A rejection is a decision, not a glitch: repeating the login would only fire
# another prompt at the same phone.
DENIED_OUTCOMES = ("PhoneAppDenied", "PhoneAppFraud")


class MicrosoftLoginError(Exception):
    pass


class MicrosoftLoginDenied(MicrosoftLoginError):
    """The sign-in was actively rejected on the phone."""


def _mfa_error(outcome, message=None):
    """Turn a Microsoft 2FA result value into a readable exception."""
    explanation = MFA_OUTCOMES.get(outcome) or message or outcome or "unknown reason"
    failure = MicrosoftLoginDenied if outcome in DENIED_OUTCOMES else MicrosoftLoginError
    return failure("2FA was not completed - %s" % explanation)


def _parse_config(text):
    """Extract the $Config={...} javascript object of an Entra ID page."""
    marker = "$Config="
    start = text.find(marker)
    if start < 0:
        return None
    start += len(marker)
    depth = 0
    for i, char in enumerate(text[start:]):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:start + i + 1])
                except ValueError:
                    return None
    return None


def _page_id(text, config):
    if config and config.get("pgid"):
        return config["pgid"]
    match = re.search(r'<meta\s+name="PageID"\s+content="([^"]+)"', text)
    return match.group(1) if match else None


def _config_error(config):
    for key in ("strServiceExceptionMessage", "sErrTxt", "sErrorCode"):
        value = config.get(key)
        if value:
            return str(value)
    return None


def _decode_url_literal(raw):
    """Undo html and javascript escaping of a url taken out of a page."""
    unescaped = raw.strip().replace("\\/", "/").replace("\\u0026", "&")
    return htmllib.unescape(unescaped)


def _safe_redirect(base_url, raw):
    """Resolve a redirect candidate, or None if it must not be followed.

    Only absolute https urls on the expected Keycloak/Microsoft hosts are
    accepted so that a manipulated page cannot send the session elsewhere.
    """
    if not raw:
        return None
    url = urljoin(base_url, _decode_url_literal(raw))
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return None
    if (parsed.hostname or "").lower() not in KEYCLOAK_REDIRECT_HOSTS:
        return None
    return url


def _page_title(text):
    try:
        tag = BeautifulSoup(text, "html.parser").find("title")
    except Exception:
        return ""
    if not tag:
        return ""
    return " ".join(tag.get_text().split())[:120]


def _keycloak_diagnostics(response):
    """Error text for an unusable Keycloak page.

    Deliberately limited to metadata: no body, no cookies, no query string -
    those carry the oauth state, the session code and the redirect target.
    """
    parsed = urlparse(response.url)
    body = response.text or ""
    content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip()
    return ("no identity provider redirect on the Keycloak page "
            "(status=%s host=%s path=%s content-type=%s title=%r body-length=%d)"
            % (response.status_code, parsed.hostname or "?", parsed.path or "/",
               content_type or "unknown", _page_title(body), len(body)))


def _print_box(message):
    """Print the 2FA number in a box, also on a console that is not utf-8.

    A plain Windows console runs on cp1252 and cannot encode the box drawing
    characters, which used to abort the login right before the MFA poll.
    """
    width = len(message) + 4
    fancy = ["╔" + "═" * width + "╗",
             "║  %s  ║" % message,
             "╚" + "═" * width + "╝"]
    plain = ["+" + "-" * width + "+",
             "|  %s  |" % message,
             "+" + "-" * width + "+"]

    encoding = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        "".join(fancy).encode(encoding)
        lines = fancy
    except (UnicodeEncodeError, LookupError):
        lines = plain

    try:
        print("\n\033[93m%s\033[0m\n" % "\n".join(lines))
    except UnicodeEncodeError:  # pragma: no cover - last resort
        print("\n%s\n" % "\n".join(plain))


def _require_tty(what):
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise MicrosoftLoginError(
            "%s needs an interactive terminal - run the login once by hand so "
            "the session gets stored, afterwards it works unattended" % what)


def _prompt(message):
    _require_tty(message.rstrip(":"))
    try:
        import questionary
        answer = questionary.text(message).ask()
        if answer is not None:
            return answer.strip()
    except Exception:
        pass
    return input("%s " % message).strip()


def _select(message, choices):
    _require_tty(message.rstrip(":"))
    try:
        import questionary
        answer = questionary.select(message, choices=choices).ask()
        if answer is not None:
            return answer
    except Exception:
        pass
    for i, choice in enumerate(choices):
        print("[%d] %s" % (i + 1, choice))
    return choices[int(_prompt("number:")) - 1]


class TerminalUI():
    """How the login talks to the user.

    Everything interactive goes through this object, so a frontend without a
    terminal can hand in its own implementation instead of having to replace
    the functions of this module.
    """

    def text(self, message):
        """Ask for a value the user has to read somewhere (a 2FA code)."""
        return _prompt(message)

    def select(self, message, choices):
        """Let the user pick one of several 2FA methods."""
        return _select(message, choices)

    def show_number(self, number):
        """Show the number matching value while the phone is asked."""
        logger.opt(colors=True).info(
            "<bold><yellow>Zahl in der Authenticator-App eingeben: %s</yellow></bold>"
            % number)
        _print_box("Authenticator-App:  %-11s" % number)

    def hide_number(self):
        """The approval is over - the number is not needed anymore."""

    def waiting(self):
        logger.info("waiting for approval in the Authenticator app ...")


class LpisSSOSession():
    """Drives the LPIS -> Keycloak -> Microsoft login and stores the cookies."""

    def __init__(self, username, password=None, sessionfile=None,
                 ms_domain="s.wu.ac.at", mfa_method=None, ui=None):
        self.username = username
        self.password = password
        self.sessionfile = sessionfile
        self.ms_domain = ms_domain
        self.mfa_method = mfa_method
        self.ui = ui or TerminalUI()

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
        })

        self.correlation_id = str(uuid.uuid4())
        self.used_stored_session = False
        self.did_interactive_login = False
        # Guards against repeating the hinted authorization request forever if
        # Keycloak keeps sending us back to the login page.
        self.idp_hint_attempted = False
        self.keycloak_forms_submitted = 0

    # ------------------------------------------------------------------ #
    # session persistence
    # ------------------------------------------------------------------ #

    @property
    def ms_username(self):
        if "@" in self.username:
            return self.username
        return "%s@%s" % (self.username, self.ms_domain)

    def load_session(self):
        if not self.sessionfile or not os.path.isfile(self.sessionfile):
            return False
        try:
            with open(self.sessionfile, "rb") as file:
                cookies = pickle.load(file)
            self.session.cookies.update(cookies)
        except Exception as error:
            logger.warning("could not load stored session: %s" % error)
            return False
        logger.info("stored session loaded from %s" % self.sessionfile)
        return True

    def save_session(self):
        if not self.sessionfile:
            return False
        directory = os.path.dirname(self.sessionfile)
        if directory and not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
        try:
            with open(self.sessionfile, "wb") as file:
                pickle.dump(self.session.cookies, file, pickle.HIGHEST_PROTOCOL)
        except Exception as error:
            logger.warning("could not save session: %s" % error)
            return False
        logger.info("session saved to %s" % self.sessionfile)
        return True

    def clear_session(self):
        self.session.cookies.clear()
        if self.sessionfile and os.path.isfile(self.sessionfile):
            try:
                os.remove(self.sessionfile)
            except OSError:
                pass

    # ------------------------------------------------------------------ #
    # public entry point
    # ------------------------------------------------------------------ #

    def login(self):
        """Run the whole chain and return the final LPIS response."""
        had_session = self.load_session()
        self.used_stored_session = had_session

        try:
            response = self._run()
        except MicrosoftLoginDenied:
            # retrying would just send a second prompt to the same phone
            raise
        except MicrosoftLoginError:
            if not had_session:
                raise
            logger.warning("stored session unusable, starting a fresh login")
            self.clear_session()
            self.used_stored_session = False
            response = self._run()

        self.save_session()
        return response

    # ------------------------------------------------------------------ #
    # the redirect chain
    # ------------------------------------------------------------------ #

    def _run(self):
        self.idp_hint_attempted = False
        self.keycloak_forms_submitted = 0
        response = self.session.get(START_URL, timeout=30)

        for _ in range(MAX_STEPS):
            host = urlparse(response.url).netloc.lower()

            if host.endswith("lpis.wu.ac.at") and "/kdcs/" in urlparse(response.url).path:
                logger.info("logged in, LPIS session: %s" % response.url)
                return response

            if host == "bach-id.wu.ac.at":
                response = self._handle_keycloak(response)
            elif host in MS_HOSTS:
                response = self._handle_microsoft(response)
            else:
                submitted = self._auto_submit(response)
                if submitted is None:
                    raise MicrosoftLoginError(
                        "unexpected page during login: %s" % response.url)
                response = submitted

        raise MicrosoftLoginError("login did not finish within %d steps" % MAX_STEPS)

    def _auto_submit(self, response):
        """Follow a self-posting html form (used for form_post responses)."""
        soup = BeautifulSoup(response.text, "html.parser")
        form = soup.find("form")
        if not form:
            return None
        action = urljoin(response.url, form.get("action") or response.url)
        data = {}
        for field in form.find_all("input"):
            name = field.get("name")
            if name:
                data[name] = field.get("value", "")
        method = (form.get("method") or "post").lower()
        if method == "get":
            return self.session.get(action, params=data, timeout=30)
        return self.session.post(action, data=data, timeout=30)

    # ------------------------------------------------------------------ #
    # Keycloak
    # ------------------------------------------------------------------ #

    def _handle_keycloak(self, response):
        # The realm only offers the "wu-microsoft" identity provider, so ask
        # Keycloak itself to jump there instead of reading its markup.
        hinted = self._request_with_idp_hint(response)
        if hinted is not None:
            return hinted

        # Before any of that the realm runs a Kerberos/SPNEGO execution. It
        # answers with 401 and a self-posting form; the identity provider only
        # appears on the page after that form has been sent.
        continued = self._submit_keycloak_form(response)
        if continued is not None:
            return continued

        target = self._keycloak_redirect_target(response)
        if target is None:
            raise MicrosoftLoginError(_keycloak_diagnostics(response))
        return self.session.get(target, timeout=30)

    def _authorization_url(self, response):
        """The OIDC authorization url this response came from, if any."""
        candidates = [response.url]
        candidates.extend(
            step.url for step in reversed(getattr(response, "history", None) or []))
        for url in candidates:
            parsed = urlparse(url)
            if (parsed.hostname or "").lower() != KEYCLOAK_HOST:
                continue
            if _AUTH_ENDPOINT_RE.search(parsed.path):
                return parsed
        return None

    def _request_with_idp_hint(self, response):
        """Repeat the authorization request once with kc_idp_hint set.

        Returns the new response, or None if the hint does not apply or was
        already tried during this run.
        """
        if self.idp_hint_attempted:
            return None

        parsed = self._authorization_url(response)
        if parsed is None:
            return None

        self.idp_hint_attempted = True

        query = parse_qsl(parsed.query, keep_blank_values=True)
        if any(key == IDP_HINT_PARAM for key, _ in query):
            return None
        query.append((IDP_HINT_PARAM, IDP_HINT_VALUE))
        target = urlunparse(parsed._replace(query=urlencode(query)))

        logger.info("Keycloak login page, retrying the authorization request "
                    "with %s=%s" % (IDP_HINT_PARAM, IDP_HINT_VALUE))
        return self.session.get(target, timeout=30)

    def _submit_keycloak_form(self, response):
        """Send Keycloak's self-posting interstitial onwards.

        Returns the new response, or None if the page is not such a form.
        A form asking the user for anything is never submitted blindly.
        """
        if self.keycloak_forms_submitted >= MAX_KEYCLOAK_FORMS:
            return None

        form = BeautifulSoup(response.text or "", "html.parser").find("form")
        if form is None:
            return None

        # a real credential form is none of our business here
        for field in form.find_all("input"):
            if (field.get("type") or "text").lower() in ("password", "text", "email"):
                return None

        action = urljoin(response.url, form.get("action") or response.url)
        parsed = urlparse(action)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() != KEYCLOAK_HOST:
            return None

        data = {}
        for field in form.find_all("input"):
            name = field.get("name")
            if name:
                data[name] = field.get("value", "")

        self.keycloak_forms_submitted += 1
        logger.info("Keycloak Kerberos step - continuing with the alternative login")

        if (form.get("method") or "post").lower() == "get":
            return self.session.get(action, params=data, timeout=30)
        return self.session.post(action, data=data, timeout=30)

    def _keycloak_redirect_target(self, response):
        """First redirect on the page that is safe to follow, or None."""
        text = response.text or ""
        for pattern in _REDIRECT_PATTERNS:
            for match in pattern.finditer(text):
                target = _safe_redirect(response.url, match.group(1))
                if target:
                    return target
        return None

    # ------------------------------------------------------------------ #
    # Microsoft Entra ID
    # ------------------------------------------------------------------ #

    def _api_headers(self, config, referer, ctx=None, flow_token=None, session_id=None):
        headers = {
            "Accept": "application/json",
            "Content-type": "application/json; charset=UTF-8",
            "canary": config.get("apiCanary", ""),
            "client-request-id": config.get("correlationId", self.correlation_id),
            "hpgact": str(config.get("hpgact", "")),
            "hpgid": str(config.get("hpgid", "")),
            "hpgrequestid": config.get("sessionId", ""),
            "Origin": "https://login.microsoftonline.com",
            "Referer": referer,
        }
        if ctx:
            headers["x-ms-ctx"] = ctx
        if flow_token:
            headers["x-ms-flowToken"] = flow_token
        if session_id:
            headers["x-ms-sessionId"] = session_id
        return headers

    def _handle_microsoft(self, response):
        config = _parse_config(response.text)
        page = _page_id(response.text, config)

        if config is None:
            submitted = self._auto_submit(response)
            if submitted is None:
                raise MicrosoftLoginError(
                    "could not read the Microsoft page at %s" % response.url)
            return submitted

        if page == "BssoInterrupt":
            # Browser-SSO probe page. The browser repeats the request that ran
            # into the interrupt: if the page carries the original post body
            # (oPostParams) it has to be posted again, otherwise the url is
            # simply requested again.
            target = urljoin(response.url, config["urlPost"])
            post_params = config.get("oPostParams")
            if post_params:
                return self.session.post(
                    target, data=post_params, timeout=30,
                    headers={"Referer": response.url,
                             "Origin": "https://login.microsoftonline.com"})
            return self.session.get(target, timeout=30)

        if page == "ConvergedSignIn":
            return self._do_password(response, config)

        if page == "ConvergedTFA":
            return self._do_mfa(response, config)

        if page == "KmsiInterrupt":
            return self._do_kmsi(response, config)

        if page == "ConvergedProofUpRedirect":
            raise MicrosoftLoginError(
                "Microsoft requires MFA setup for this account - "
                "please complete it once in a browser (https://aka.ms/mfasetup)")

        error = _config_error(config)
        raise MicrosoftLoginError(
            "unhandled Microsoft page '%s'%s" % (page, ": %s" % error if error else ""))

    def _do_password(self, response, config):
        error = _config_error(config)
        if self.did_interactive_login and error:
            raise MicrosoftLoginError("Microsoft rejected the login: %s" % error)

        if not self.password:
            raise MicrosoftLoginError(
                "Microsoft asks for a password but none was provided")

        self.did_interactive_login = True
        username = self.ms_username
        logger.info("signing in at Microsoft as %s" % username)

        flow_token = config.get("sFT", "")
        credential_type_url = config.get("urlGetCredentialType")
        if credential_type_url:
            payload = {
                "username": username,
                "isOtherIdpSupported": True,
                "checkPhones": False,
                "isRemoteNGCSupported": True,
                "isCookieBannerShown": False,
                "isFidoSupported": False,
                "originalRequest": config.get("sCtx", ""),
                "country": config.get("country", "AT"),
                "forceotclogin": False,
                "isExternalFederationDisallowed": False,
                "isRemoteConnectSupported": False,
                "federationFlags": 0,
                "isSignup": False,
                "flowToken": flow_token,
            }
            result = self.session.post(
                credential_type_url, json=payload, timeout=30,
                headers=self._api_headers(config, response.url))
            try:
                body = result.json()
            except ValueError:
                body = {}
            if body.get("IfExistsResult") == 1:
                raise MicrosoftLoginError("unknown Microsoft account: %s" % username)
            flow_token = body.get("FlowToken") or flow_token

        data = {
            "i13": "0",
            "login": username,
            "loginfmt": username,
            "type": "11",
            "LoginOptions": "3",
            "lrt": "",
            "lrtPartition": "",
            "hisRegion": "",
            "hisScaleUnit": "",
            "passwd": self.password,
            "ps": "2",
            "psRNGCDefaultType": "",
            "psRNGCEntropy": "",
            "psRNGCSLK": "",
            "canary": config.get("canary", ""),
            "ctx": config.get("sCtx", ""),
            "hpgrequestid": config.get("sessionId", ""),
            "flowToken": flow_token,
            "PPSX": "",
            "NewUser": "1",
            "FoundMSAs": "",
            "fspost": "0",
            "i21": "0",
            "CookieDisclosure": "0",
            "IsFidoSupported": "0",
            "isSignupPost": "0",
            "DfpArtifact": "",
        }
        return self.session.post(
            urljoin(response.url, config["urlPost"]), data=data, timeout=30,
            headers={"Referer": response.url,
                     "Origin": "https://login.microsoftonline.com"})

    # -- MFA ----------------------------------------------------------- #

    def _pick_method(self, proofs):
        if not proofs:
            raise MicrosoftLoginError("no MFA method offered by Microsoft")

        by_id = {proof.get("authMethodId"): proof for proof in proofs}

        if self.mfa_method:
            if self.mfa_method not in by_id:
                raise MicrosoftLoginError(
                    "MFA method '%s' not available (offered: %s)"
                    % (self.mfa_method, ", ".join(by_id)))
            return by_id[self.mfa_method]

        if len(proofs) == 1:
            return proofs[0]

        for proof in proofs:
            if proof.get("isDefault"):
                return proof

        labels = ["%s (%s)" % (proof.get("authMethodId"), proof.get("display", ""))
                  for proof in proofs]
        chosen = self.ui.select("2FA-Methode waehlen:", labels)
        if chosen not in labels:
            raise MicrosoftLoginError("no 2FA method selected")
        return proofs[labels.index(chosen)]

    def _do_mfa(self, response, config):
        proof = self._pick_method(config.get("arrUserProofs") or [])
        method_id = proof.get("authMethodId")
        logger.info("2FA method: %s (%s)" % (method_id, proof.get("display", "")))

        begin = self.session.post(
            config["urlBeginAuth"], timeout=30,
            headers=self._api_headers(config, response.url),
            json={
                "AuthMethodId": method_id,
                "Method": "BeginAuth",
                "ctx": config.get("sCtx", ""),
                "flowToken": config.get("sFT", ""),
            })
        result = begin.json()
        if not result.get("Success"):
            outcome = result.get("ResultValue")
            raise MicrosoftLoginError(
                "could not start 2FA - %s"
                % (MFA_OUTCOMES.get(outcome) or result.get("Message") or outcome))

        ctx = result.get("Ctx") or config.get("sCtx", "")
        flow_token = result.get("FlowToken") or config.get("sFT", "")
        session_id = result.get("SessionId")
        entropy = result.get("Entropy")

        poll_start = int(time.time() * 1000)

        if method_id in OTP_METHODS:
            code = self.ui.text("2FA-Code eingeben:")
            end = self.session.post(
                config["urlEndAuth"], timeout=30,
                headers=self._api_headers(config, response.url, ctx, flow_token, session_id),
                json={
                    "Method": "EndAuth",
                    "SessionId": session_id,
                    "FlowToken": flow_token,
                    "Ctx": ctx,
                    "AuthMethodId": method_id,
                    "AdditionalAuthData": code,
                    "PollCount": 1,
                })
            result = end.json()
            if not result.get("Success"):
                raise _mfa_error(result.get("ResultValue"), result.get("Message"))
            ctx = result.get("Ctx") or ctx
            flow_token = result.get("FlowToken") or flow_token
        else:
            if entropy is not None:
                self.ui.show_number(entropy)
            else:
                self.ui.waiting()

            try:
                ctx, flow_token = self._poll_mfa(
                    response, config, method_id, ctx, flow_token, session_id)
            finally:
                self.ui.hide_number()

        poll_end = int(time.time() * 1000)

        data = {
            "type": "22",
            "request": ctx,
            "mfaLastPollStart": str(poll_start),
            "mfaLastPollEnd": str(poll_end),
            "mfaAuthMethod": method_id,
            "login": self.ms_username,
            "flowToken": flow_token,
            "hpgrequestid": config.get("sessionId", ""),
            "sacxt": "",
            "hideSmsInMfaProofs": "false",
            "canary": config.get("canary", ""),
        }
        return self.session.post(
            urljoin(response.url, config["urlPost"]), data=data, timeout=30,
            headers={"Referer": response.url,
                     "Origin": "https://login.microsoftonline.com"})

    def _poll_mfa(self, response, config, method_id, ctx, flow_token, session_id):
        timeout = (config.get("iPollingTimeout") or 300000) / 1000.0
        deadline = time.time() + min(timeout, 300)
        poll_count = 0
        last_start = last_end = None

        while time.time() < deadline:
            poll_count += 1
            params = {"authMethodId": method_id, "pollCount": poll_count}
            if last_start is not None:
                params["lastPollStart"] = last_start
                params["lastPollEnd"] = last_end

            last_start = int(time.time() * 1000)
            poll = self.session.get(
                config["urlEndAuth"], params=params, timeout=30,
                headers=self._api_headers(config, response.url, ctx, flow_token, session_id))
            last_end = int(time.time() * 1000)

            result = poll.json()
            flow_token = result.get("FlowToken") or flow_token
            ctx = result.get("Ctx") or ctx
            outcome = result.get("ResultValue")

            if result.get("Success"):
                logger.opt(colors=True).info("<green>2FA confirmed</green>")
                return ctx, flow_token

            if outcome != "AuthenticationPending":
                raise _mfa_error(outcome, result.get("Message"))

            time.sleep(POLL_INTERVAL)

        raise MicrosoftLoginError("2FA timed out - no approval on the phone")

    def _do_kmsi(self, response, config):
        # "Stay signed in?" -> yes. This is what makes the stored session
        # survive, so the 2FA prompt only happens once.
        data = {
            "LoginOptions": "1",
            "type": "28",
            "ctx": config.get("sCtx", ""),
            "hpgrequestid": config.get("sessionId", ""),
            "flowToken": config.get("sFT", ""),
            "canary": config.get("canary", ""),
        }
        return self.session.post(
            urljoin(response.url, config["urlPost"]), data=data, timeout=30,
            headers={"Referer": response.url,
                     "Origin": "https://login.microsoftonline.com"})
