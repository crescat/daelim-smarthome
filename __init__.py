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
from .helper import request_data, get_csrf, get_daelim_elife, get_expire_time, request_html, encrypt, decrypt

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.LIGHT,
    #Platform.CLIMATE,
    #Platform.SENSOR,
    #Platform.FAN,
    #Platform.SWITCH,
    #Platform.BUTTON,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up daelim-smarthome from a config entry."""
    csrf = await hass.async_add_executor_job(get_csrf)
    daelim_elife = await hass.async_add_executor_job(
        get_daelim_elife,
        csrf,
        entry.data["email"],
        entry.data["password"])
    coordinator = MyCoordinator(hass, entry, csrf, daelim_elife)
    await coordinator.async_config_entry_first_refresh()
    hass.data[DOMAIN] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


class MyCoordinator(update_coordinator.DataUpdateCoordinator):
    """My custom coordinator."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, csrf, daelim_elife) -> None:
        """Initialize my coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            # Name of the data. For logging purposes.
            name="daelim_smarthome",
        )
        self.entry = entry
        self.csrf = csrf
        self.daelim_elife = daelim_elife
        self.device_list = []
        self.websocket_auth = {}

        asyncio.ensure_future(self._connect_websocket())

    def request_device_status(self, device_uid, device_type):
        return self.post_with_csrf_and_daelim_elife(
            "/controls/device/status.ajax",
            {"uid": device_uid,"type": device_type}
        )

    def refresh_daelim_elife_if_expired(self):
        expire_time = get_expire_time(self.daelim_elife)
        currrent_time = datetime.datetime.now()
        if currrent_time > expire_time:
            self.csrf = get_csrf()
            self.daelim_elife = get_daelim_elife(
                self.csrf,
                self.entry.data["email"],
                self.entry.data["password"])

    def post_with_csrf_and_daelim_elife(self, url, json_data):
        self.refresh_daelim_elife_if_expired()
        header = {"_csrf": self.csrf, "daelim_elife": self.daelim_elife}
        return request_data(
            url,
            header,
            json_data
        )

    def get_data_from_html(self):
        current_time = (datetime.datetime.now() + datetime.timedelta(hours=9)).strftime("%Y%m%d%H%M%S")
        authorization = "Bearer " + encrypt(self.daelim_elife + "::" + current_time)
        return request_html(
            "/main/home.do",
            {"Authorization": authorization},
            {}).text

    def find_device_list_from_html(self, html):
        regex = r"const _deviceListByType = '([^']+)'"
        match = re.search(regex, html)
        if match:
            return json.loads(match.group(1))
        raise Exception("Cannot find device list!")

    def find_keys_from_html(self, html):
        json = {}
        for key in ['roomKey', 'userKey', 'accessToken']:
            regex = fr"'{key}': '([^']+)'"
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
        html = await self.hass.async_add_executor_job(self.get_data_from_html)
        with open("response2.html", "w") as f:
            f.write(html)

        self.device_list = self.find_device_list_from_html(html)
        self.websocket_auth = self.find_keys_from_html(html)

    async def _connect_websocket(self):
        """Establish WebSocket connection."""
        url = "wss://smartelife.apt.co.kr/ws/data"
        data = self.websocket_auth | {
                "data":[{"type":"light"},{"type":"heat"},{"type":"alloffswitch"},{"type":"smartdoor"},{"type":"aircon"}]
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
                self.async_set_updated_data(data)
        except Exception as e:
            pass