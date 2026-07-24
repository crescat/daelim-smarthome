import base64
import datetime
import json
import logging
import re
import requests
import threading
import uuid
from requests.adapters import HTTPAdapter, Retry
from Crypto.Cipher import AES
from Crypto import Random
from .const import (
    CONNECT_TIMEOUT,
    FAST_READ_TIMEOUT,
    READ_TIMEOUT,
    RETRY,
    API_PREFIX,
    KEY,
    IV,
    BS,
    REFRESH_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

json_header = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 9_2 like Mac OS X) AppleWebKit/601.1.46 (KHTML, like Gecko) Mobile/13C75 DAELIM/IOS",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "application/json",
}

html_header = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 9_2 like Mac OS X) AppleWebKit/601.1.46 (KHTML, like Gecko) Mobile/13C75 DAELIM/IOS",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "X-Requested-With": "com.daelim.elife",
}


class Credentials:
    """The login session with the Daelim cloud.

    The server keeps a single active session per account and the login
    token (daelim_elife) lives only ~15 minutes, so the session has to be
    kept alive. Two operations do that, both serialized behind a lock:

    - login(): bootstrap from username/password. Mints a new cloud
      (websocket) token server-side, invalidating the previous one, so it
      is the cold-start path only.
    - refresh(): reload /main/home.do, which re-embeds a freshly minted
      token without touching the cloud token, leaving the live websocket
      undisturbed. This is the routine keep-alive.
    """

    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.device_id = None
        self.websocket_keys = None
        self.csrf = None
        self.daelim_elife = None
        self.expire_time = None
        self.refreshed_at = None
        self._home_html = None
        self._lock = threading.RLock()

    @classmethod
    def from_dict(cls, dict):
        cred = cls(dict["username"], dict["password"])
        cred.device_id = dict["device_id"]
        cred.websocket_keys = dict.get("websocket_keys")
        cred.csrf = dict.get("csrf")
        cred.daelim_elife = dict.get("daelim_elife")
        cred.expire_time = dict.get("expire_time") and datetime.datetime.fromtimestamp(
            dict["expire_time"]
        )
        return cred

    def to_dict(self):
        return {
            "username": self.username,
            "password": self.password,
            "device_id": self.device_id,
            "websocket_keys": self.websocket_keys,
            "csrf": self.csrf,
            "daelim_elife": self.daelim_elife,
            "expire_time": self.expire_time.timestamp() if self.expire_time else None,
        }

    def login(self):
        """Bootstrap a fresh session from username/password.

        The heavy path: it mints a new cloud (websocket) token
        server-side and invalidates the previous one, so it runs only
        when there is no live session to slide (cold start or dead token).
        """
        if not self.device_id:
            self.device_id = str(uuid.uuid4())
        self.refresh_csrf()
        response = request_ajax(
            "/login.ajax", {"_csrf": self.csrf}, self.get_login_json()
        )
        self._adopt_token(response["daelim_elife"])
        # login() does not load home.do, so anything derived from it is
        # stale until refetched.
        self._home_html = None
        self.websocket_keys = None

    def refresh(self):
        """Slide the current session forward without a full re-login.

        Reloading /main/home.do re-embeds a freshly minted 15-minute
        token, exactly as the app's page navigation does. The cloud token
        is left in place, so the live websocket keeps running. The csrf is
        renewed too, to keep the whole HTTP-auth set fresh now that full
        logins are rare. If the server bounced us to a login page instead
        (session already gone), fall back to login().
        """
        self.refresh_csrf()
        html = self._fetch_home_html()
        token = self._scrape(html, "daelim_elife")
        if not token:
            self.login()
            return
        self._adopt_token(token)

    def _adopt_token(self, token):
        """Take a freshly minted daelim_elife as the current session."""
        self.daelim_elife = token
        self.expire_time = get_expire_time(token)
        self.refreshed_at = datetime.datetime.now()

    def refresh_csrf(self):
        response = request_ajax("/common/nativeToken.ajax", {}, {})
        self.csrf = response["value"]

    def ensure_fresh(self):
        """Guarantee a usable session before a request.

        No token or an expired one: bootstrap with login(). A live token
        older than REFRESH_INTERVAL: slide it with the cheap refresh().
        Otherwise it is young enough to use as-is.
        """
        with self._lock:
            now = datetime.datetime.now()
            if (
                not self.daelim_elife
                or not self.expire_time
                or now >= self.expire_time
            ):
                self.login()
            elif not self.refreshed_at or now - self.refreshed_at >= REFRESH_INTERVAL:
                self.refresh()

    def force_login(self):
        """Discard local session state and log in again.

        Used when the server rejects a request despite the token looking
        valid locally (e.g. logged out for inactivity). This invalidates
        the previous session's cloud token server-side, so use it only
        when the current session is known dead.
        """
        with self._lock:
            self.daelim_elife = None
            self.expire_time = None
            self.ensure_fresh()

    def bearer_token(self):
        with self._lock:
            self.ensure_fresh()
            return self._bearer_token()

    def _bearer_token(self):
        """Bearer for the current token, assuming freshness was already
        ensured. Used inside refresh() itself, where calling the public
        bearer_token() would recurse through ensure_fresh()."""
        now_in_kst = datetime.datetime.now() + datetime.timedelta(hours=9)
        return encrypt(
            "{}::{}".format(
                self.daelim_elife,
                now_in_kst.strftime("%Y%m%d%H%M%S"),
            )
        )

    def daelim_header(self):
        with self._lock:
            self.ensure_fresh()
            return {"_csrf": self.csrf, "daelim_elife": self.daelim_elife}

    def _fetch_home_html(self):
        """GET and cache /main/home.do with the current bearer.

        The page carries the device list, the websocket keys, and a fresh
        token, so a fetch invalidates the derived websocket keys to force
        them to re-extract from the new copy.
        """
        content = get_html(
            "/main/home.do", {"Authorization": f"Bearer {self._bearer_token()}"}
        ).text
        _LOGGER.debug("Got HTML from /main/home.do\n\n%s", content)
        self._home_html = content
        self.websocket_keys = None
        return content

    def main_home_html(self, force_refresh=False):
        """also used by coordinator to get device list without re-requesting."""
        with self._lock:
            self.ensure_fresh()
            if self._home_html and not force_refresh:
                return self._home_html
            return self._fetch_home_html()

    def websocket_keys_json(self, force_refresh=False):
        with self._lock:
            self.ensure_fresh()
            if self.websocket_keys and not force_refresh:
                return self.websocket_keys
            html = self.main_home_html(force_refresh)
            keys = {}
            for key in ["roomKey", "userKey", "accessToken"]:
                value = self._scrape(html, key)
                if value is None:
                    raise Exception(f"Cannot find {key}!")
                keys[key] = value
            self.websocket_keys = keys
            return self.websocket_keys

    @staticmethod
    def _scrape(html, key):
        """Pull a single-quoted `'key': 'value'` field out of home.do."""
        match = re.search(rf"'{key}': '([^']+)'", html)
        return match[1] if match else None

    def get_csrf(self):
        return self.csrf

    def get_login_json(self):
        login_json = {
            "input_memb_uid": "",
            "input_hm_cd": "",
            "input_acc_os_info": "ios",
            "input_dv_osver_info": "15.4.1",
            "input_auto_login": "on",
            "input_dv_make_info": "Apple",
            "input_version": "1.1.4",
            "input_push_token": "",
            "input_flag": "login",
            "input_dv_model_info": "iPhone12,8",
        }
        return login_json | {
            "input_dv_uuid": self.device_id,
            "input_username": encrypt(self.username),
            "input_password": encrypt(self.password),
        }


def get_json_header():
    return json_header


def get_html_header():
    return html_header


def base64ToString(b):
    import base64

    return base64.b64decode(b).decode("utf-8")


def get_expire_time(token):
    data = token.split(".")[1]
    decoded = json.loads(base64ToString(data))
    exp_time = decoded["exp"]
    return datetime.datetime.fromtimestamp(exp_time)


_http_session = None


def http_session():
    """The shared keep-alive session to the Daelim API.

    Reusing one session keeps the TLS connection pooled, so a control
    request after hours of idling doesn't pay a fresh handshake.
    """
    global _http_session
    if _http_session is None:
        s = requests.Session()
        retries = Retry(
            total=RETRY,
            # Read timeouts are recovered by _send_with_recovery on a fresh
            # connection instead: retrying within the same pool can just hand
            # back another socket that a NAT/firewall silently dropped.
            read=0,
            # 0s, 2s, 4s...
            backoff_factor=1,
            status_forcelist=[500, 502, 503, 504],
            # allow retry on POST requests
            allowed_methods=None,
        )
        s.mount(API_PREFIX, HTTPAdapter(max_retries=retries))
        _http_session = s
    return _http_session


def reset_http_session():
    """Throw the pooled connection away so the next request dials a fresh one.

    A keep-alive socket dropped during idle by a NAT/firewall stays
    ESTABLISHED on our side with no FIN to detect, so a request on it just
    hangs until the read timeout. Once that happens we discard the whole
    pool rather than risk handing out another dead connection.
    """
    global _http_session
    if _http_session is not None:
        _http_session.close()
        _http_session = None


def _send_with_recovery(send):
    """Run send(session, timeout), retrying once on a fresh connection.

    A stale pooled socket can't be told apart from a live one up front, so
    the first attempt fails fast (FAST_READ_TIMEOUT). On any timeout or
    connection error we drop the pool and redial, giving the fresh, known-good
    connection a far more patient budget (READ_TIMEOUT) since a cold server
    can be slow to answer.
    """
    try:
        return send(http_session(), (CONNECT_TIMEOUT, FAST_READ_TIMEOUT))
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        reset_http_session()
        return send(http_session(), (CONNECT_TIMEOUT, READ_TIMEOUT))


def request_ajax(path, header, params):
    url = API_PREFIX + path
    header = get_json_header() | header
    response = _send_with_recovery(
        lambda s, timeout: s.post(url, headers=header, json=params, timeout=timeout)
    )

    if "content-type" not in response.headers:
        raise TypeError("response has no content-type header")

    content_type = response.headers["content-type"]
    if "application/json" in content_type:
        return response.json()

    raise TypeError("response is not json")


def get_html(path, header):
    url = API_PREFIX + path
    header = get_html_header() | header
    return _send_with_recovery(
        lambda s, timeout: s.get(url, headers=header, timeout=timeout)
    )


def unpad(s):
    return s[: -ord(s[len(s) - 1 :])]


def pad(s):
    return s + ((BS - len(s) % BS) * chr(BS - len(s) % BS)).encode("utf-8")


def encrypt(raw):
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    raw = pad(raw)
    cipher = AES.new(KEY, AES.MODE_CBC, IV)
    return base64.b64encode(cipher.encrypt(raw)).decode("utf-8")


def decrypt(enc):
    enc = base64.b64decode(enc)
    cipher = AES.new(KEY, AES.MODE_CBC, IV)
    return unpad(cipher.decrypt(enc))


def get_location(device_data):
    if "location_name_alias" in device_data:
        return device_data["location_name_alias"]
    return device_data["location_name"]
