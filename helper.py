import base64
import datetime
import json
import logging
import re
import requests
import uuid
from requests.adapters import HTTPAdapter, Retry
from Crypto.Cipher import AES
from Crypto import Random
from .const import TIMEOUT, RETRY, API_PREFIX, KEY, IV, BS

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
    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.device_id = None
        self.websocket_keys = None
        self.csrf = None
        self.daelim_elife = None
        self.expire_time = None

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
        if not self.device_id:
            self.device_id = str(uuid.uuid4())
        if not self.csrf:
            self.refresh_csrf()
        response = request_ajax(
            "/login.ajax", {"_csrf": self.csrf}, self.get_login_json()
        )
        self.daelim_elife = response["daelim_elife"]
        self.expire_time = get_expire_time(self.daelim_elife)

    def refresh_csrf(self):
        response = request_ajax("/common/nativeToken.ajax", {}, {})
        self.csrf = response["value"]

    def ensure_logged_in(self):
        now = datetime.datetime.now()
        if not self.expire_time or now > self.expire_time or not self.daelim_elife:
            self.refresh_csrf()
            self.login()

    def bearer_token(self):
        self.ensure_logged_in()
        now_in_kst = datetime.datetime.now() + datetime.timedelta(hours=9)
        return encrypt(
            "{}::{}".format(
                self.daelim_elife,
                now_in_kst.strftime("%Y%m%d%H%M%S"),
            )
        )

    def daelim_header(self):
        self.ensure_logged_in()
        return {"_csrf": self.csrf, "daelim_elife": self.daelim_elife}

    def main_home_html(self, force_refresh=False, _cache={}):
        """also used by coordinator to get device list without re-requesting."""
        if "value" in _cache and not force_refresh:
            return _cache["value"]
        bearer_token = self.bearer_token()

        content = get_html(
            "/main/home.do", {"Authorization": f"Bearer {bearer_token}"}
        ).text
        _LOGGER.debug("Got HTML from /main/home.do\n\n%s", content)
        _cache["value"] = content
        return content

    def websocket_keys_json(self, force_refresh=False):
        if self.websocket_keys and not force_refresh:
            return self.websocket_keys
        self.ensure_logged_in()
        html = self.main_home_html(force_refresh)
        keys = {}
        for key in ["roomKey", "userKey", "accessToken"]:
            regex = rf"'{key}': '([^']+)'"
            match = re.search(regex, html)
            if match:
                keys[key] = match[1]
            else:
                raise Exception(f"Cannot find {key}!")
        self.websocket_keys = keys
        return self.websocket_keys

    def get_csrf(self):
        return self.csrf

    def get_login_json(self):
        login_json = {
            "input_memb_uid": "",
            "input_hm_cd": "",
            "input_acc_os_info": "ios",
            "input_dv_osver_info": "15.4.1",
            "input_auto_login": "off",
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


def request_ajax(path, header, params):
    url = API_PREFIX + path
    header = get_json_header() | header
    s = requests.Session()
    retries = Retry(
        total=RETRY,
        # 0s, 10s, 20s, 40s, 80s...
        backoff_factor=5,
        status_forcelist=[500, 502, 503, 504],
        # allow retry on POST requests
        allowed_methods=None,
    )

    s.mount(API_PREFIX, HTTPAdapter(max_retries=retries))
    response = s.post(url, headers=header, json=params, timeout=TIMEOUT)

    if "content-type" not in response.headers:
        raise TypeError("response has no content-type header")

    content_type = response.headers["content-type"]
    if "application/json" in content_type:
        return response.json()

    raise TypeError("response is not json")


def get_html(path, header):
    url = API_PREFIX + path
    header = get_html_header() | header
    s = requests.Session()
    retries = Retry(
        total=RETRY,
        # 0s, 10s, 20s, 40s, 80s...
        backoff_factor=5,
        status_forcelist=[500, 502, 503, 504],
    )
    s.mount(API_PREFIX, HTTPAdapter(max_retries=retries))
    return s.get(url, headers=header, timeout=TIMEOUT)


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
