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
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from logger import logger

START_URL = "https://lpis.wu.ac.at/lpis"

MS_HOSTS = (
    "login.microsoftonline.com",
    "login.microsoft.com",
    "login.live.com",
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


class MicrosoftLoginError(Exception):
    pass


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


class LpisSSOSession():
    """Drives the LPIS -> Keycloak -> Microsoft login and stores the cookies."""

    def __init__(self, username, password=None, sessionfile=None,
                 ms_domain="s.wu.ac.at", mfa_method=None):
        self.username = username
        self.password = password
        self.sessionfile = sessionfile
        self.ms_domain = ms_domain
        self.mfa_method = mfa_method

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
        })

        self.correlation_id = str(uuid.uuid4())
        self.used_stored_session = False
        self.did_interactive_login = False

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
        text = response.text

        # The login page has a single identity provider ("wu-microsoft") and
        # redirects to it via javascript.
        match = re.search(r"decodeHTMLEntities\(\s*'([^']+)'\s*\)", text)
        if not match:
            match = re.search(r'href="([^"]*broker/[^"/]+/login\?[^"]*)"', text)
        if not match:
            error = BeautifulSoup(text, "html.parser").find(
                "span", {"class": "kc-feedback-text"})
            raise MicrosoftLoginError(
                "no identity provider link on the Keycloak page (%s)"
                % (error.get_text(strip=True) if error else response.url))

        target = htmllib.unescape(match.group(1)).replace("\\/", "/")
        return self.session.get(urljoin(response.url, target), timeout=30)

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
        chosen = _select("2FA-Methode waehlen:", labels)
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
            raise MicrosoftLoginError(
                "could not start 2FA: %s" % (result.get("Message") or result.get("ResultValue")))

        ctx = result.get("Ctx") or config.get("sCtx", "")
        flow_token = result.get("FlowToken") or config.get("sFT", "")
        session_id = result.get("SessionId")
        entropy = result.get("Entropy")

        poll_start = int(time.time() * 1000)

        if method_id in OTP_METHODS:
            code = _prompt("2FA-Code eingeben:")
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
                raise MicrosoftLoginError(
                    "2FA failed: %s" % (result.get("Message") or result.get("ResultValue")))
            ctx = result.get("Ctx") or ctx
            flow_token = result.get("FlowToken") or flow_token
        else:
            if entropy is not None:
                logger.opt(colors=True).info(
                    "<bold><yellow>Zahl in der Authenticator-App eingeben: %s</yellow></bold>"
                    % entropy)
                print("\n\033[93m╔══════════════════════════════════╗")
                print("║  Authenticator-App:  %-11s ║" % entropy)
                print("╚══════════════════════════════════╝\033[0m\n")
            else:
                logger.info("waiting for approval in the Authenticator app ...")

            ctx, flow_token = self._poll_mfa(
                response, config, method_id, ctx, flow_token, session_id)

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
                raise MicrosoftLoginError(
                    "2FA failed: %s" % (result.get("Message") or outcome))

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
