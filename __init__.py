"""The daelim-smarthome integration."""

from __future__ import annotations
from datetime import timedelta
from websockets.asyncio.client import connect
import logging
import async_timeout
import asyncio
import websockets
import json
import datetime
import ssl
import re

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import update_coordinator
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .helper import (
    request_ajax,
    login,
    get_html,
    encrypt,
    decrypt,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.LIGHT,
    # Platform.CLIMATE,
    # Platform.SENSOR,
    # Platform.FAN,
    # Platform.SWITCH,
    # Platform.BUTTON,
]


MESSAGE_LOGGED_OUT = "장시간 미사용으로 로그아웃 되었습니다."


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up daelim-smarthome from a config entry."""
    login_result = entry.data["login_result"]
    coordinator = MyCoordinator(hass, entry, login_result)

    await coordinator.async_config_entry_first_refresh()
    hass.data[DOMAIN] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


class MyCoordinator(update_coordinator.DataUpdateCoordinator):
    """My custom coordinator."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, login_result) -> None:
        """Initialize my coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            # Name of the data. For logging purposes.
            name="daelim_smarthome",
        )
        self.entry = entry
        self.login_result = login_result
        self.device_list = []
        self.websocket_auth = {}

    def request_device_status(self, device_uid, device_type):
        return self.request_ajax(
            "/controls/device/status.ajax", {"uid": device_uid, "type": device_type}
        )

    def refresh_daelim_elife_if_expired(self):
        currrent_time = datetime.datetime.now()
        if currrent_time > self.login_result["expires_at"]:
            self.login_result = login(
                self.entry.data["email"],
                self.entry.data["password"],
                device_id=self.login_result["device_id"],
            )

    def request_ajax(self, url, json_data):
        self.refresh_daelim_elife_if_expired()
        header = {
            "_csrf": self.login_result["csrf"],
            "daelim_elife": self.login_result["daelim_elife"],
        }
        return request_ajax(url, header, json_data)

    def get_html(self, path):
        self.refresh_daelim_elife_if_expired()
        now_in_kst = datetime.datetime.now() + datetime.timedelta(hours=9)
        auth_token = encrypt(
            "{}::{}".format(
                self.login_result["daelim_elife"],
                now_in_kst.strftime("%Y%m%d%H%M%S"),
            )
        )
        return get_html(path, {"Authorization": f"Bearer {auth_token}"}).text

    def get_device_list(self):
        return self.get_html("/main/home.do")

    def find_device_list_from_html(self, html):
        regex = r"const _deviceListByType = '([^']+)'"
        match = re.search(regex, html)
        if match:
            return json.loads(match.group(1))
        _LOGGER.warning("failed to find device list\n\n%s" % html)
        raise Exception("Cannot find device list!")

    def find_keys_from_html(self, html):
        json = {}
        for key in ["roomKey", "userKey", "accessToken"]:
            regex = rf"'{key}': '([^']+)'"
            match = re.search(regex, html)
            if match:
                json[key] = match[1]
            else:
                raise Exception(f"Cannot find {key}!")
        return json

    async def _async_update_data(self):
        """Fetch data from API endpoint.

        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        html = await self.hass.async_add_executor_job(self.get_device_list)
        _LOGGER.debug("Got HTML from /main/home.do\n\n%s", html)
        self.device_list = self.find_device_list_from_html(html)
        self.websocket_auth = self.find_keys_from_html(html)
        self.hass.async_create_background_task(
            self._connect_websocket(), "daelim-websocket"
        )

    async def _connect_websocket(self):
        """Establish WebSocket connection."""
        url = "wss://smartelife.apt.co.kr/ws/data"
        data = self.websocket_auth | {
            "data": [
                {"type": "light"},
                {"type": "heat"},
                {"type": "alloffswitch"},
                {"type": "smartdoor"},
                {"type": "aircon"},
            ]
        }
        json_data = json.dumps(data)

        while True:
            try:
                async with connect(url) as websocket:
                    await websocket.send(json_data)
                    while True:
                        data = await websocket.recv()
                        await self.handle_websocket_message(data)
            except websockets.exceptions.ConnectionClosed:
                # restart connection
                pass
            except TimeoutError:
                pass
            except ssl.SSLError:
                pass

    async def handle_websocket_message(self, message):
        """Handle incoming WebSocket messages."""
        try:
            data = json.loads(message)
            if "header" in data:
                # this is a response, ignore it
                pass
            elif "action" in data:
                _LOGGER.debug("Received websocket message: %s", message)
                self.async_set_updated_data(data)
        except Exception as e:
            pass
