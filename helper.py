import base64
import datetime
import json
import requests
from requests.adapters import HTTPAdapter, Retry
from Crypto.Cipher import AES
from Crypto import Random
from .const import TIMEOUT, RETRY, API_PREFIX, KEY, IV, BS

json_header = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 9_2 like Mac OS X) AppleWebKit/601.1.46 (KHTML, like Gecko) Mobile/13C75 DAELIM/IOS",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "application/json",
    }

html_header = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 9_2 like Mac OS X) AppleWebKit/601.1.46 (KHTML, like Gecko) Mobile/13C75 DAELIM/IOS",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

login_json = {
    "input_memb_uid": "",
    "input_hm_cd": "",
    "input_acc_os_info": "ios",
    "input_dv_osver_info": "15.4.1",
    "input_dv_uuid": "6B965DC2-0F2C-4EEE-B43C-93399801C727",
    "input_auto_login": "off",
    "input_dv_make_info": "Apple",
    "input_version": "1.1.4",
    "input_push_token": "",
    "input_flag": "login",
    "input_dv_model_info": "iPhone12,8"
}

def get_csrf():
    response = request_data(
        "/common/nativeToken.ajax",
        {},
        {})
    return response["value"]

def get_daelim_elife(csrf, username, password):
    response =  request_data(
        "/login.ajax",
        {"_csrf": csrf},
        get_login_json(username, password))
    return response["daelim_elife"]

def get_json_header():
    return json_header

def get_html_header():
    return html_header

def get_login_json(username, password):
    return login_json | {
        "input_username": encrypt(username),
        "input_password": encrypt(password)
        }

def base64ToString(b):
    import base64
    return base64.b64decode(b).decode('utf-8')

def get_expire_time(token):
    data = token.split(".")[1]
    decoded = json.loads(base64ToString(data))
    exp_time = decoded["exp"]
    return datetime.datetime.fromtimestamp(exp_time)

def request_data(path, header, params):
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
    response = s.post(
        url,
        headers=header,
        json=params,
        timeout=TIMEOUT)

    content_type = response.headers["content-type"]
    if "application/json" in content_type:
        return response.json()

    raise TypeError('response is not json')

def request_html(path, header, params):
    url = API_PREFIX + path
    header = get_html_header() | header
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
    return s.post(
        url,
        headers=header,
        json=params,
        timeout=TIMEOUT)


def unpad(s):
    print(s)
    return s[:-ord(s[len(s)-1:])]

def pad(s):
    return s + ((BS - len(s) % BS) * chr(BS - len(s) % BS)).encode('utf-8')

def encrypt(raw):
    if isinstance(raw, str):
        raw = raw.encode('utf-8')
    raw = pad(raw)
    cipher = AES.new(KEY, AES.MODE_CBC, IV)
    return base64.b64encode(cipher.encrypt(raw)).decode('utf-8')

def decrypt(enc):
    enc = base64.b64decode(enc)
    cipher = AES.new(KEY, AES.MODE_CBC, IV)
    return unpad(cipher.decrypt(enc))
