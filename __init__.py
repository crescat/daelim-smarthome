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
from .helper import request_ajax, get_html, Credentials

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.LIGHT,
    Platform.SWITCH,
]


MESSAGE_LOGGED_OUT = "장시간 미사용으로 로그아웃 되었습니다."
MESSAGE_WEBSOCKET_TOKEN_EXPIRED = "만료된 클라우드토큰 입니다."


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up daelim-smarthome from a config entry."""
    credentials = Credentials.from_dict(entry.data["credentials"])
    coordinator = MyCoordinator(hass, entry, credentials)

    await coordinator.async_config_entry_first_refresh()
    hass.data[DOMAIN] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


class MyCoordinator(update_coordinator.DataUpdateCoordinator):
    """My custom coordinator."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, credentials: Credentials
    ) -> None:
        """Initialize my coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            # Name of the data. For logging purposes.
            name="daelim_smarthome",
        )
        self.entry = entry
        self.credentials = credentials
        self.device_list = []

    def request_device_status(self, device_uid, device_type):
        return self.request_ajax(
            "/controls/device/status.ajax", {"uid": device_uid, "type": device_type}
        )

    def request_ajax(self, url, json_data):
        header = self.credentials.daelim_header()
        return request_ajax(url, header, json_data)

    def get_html(self, path):
        bearer_token = self.credentials.bearer_token()
        return get_html(path, {"Authorization": f"Bearer {bearer_token}"}).text

    def find_device_list_from_html(self, html):
        regex = r"const _deviceListByType = '([^']+)'"
        match = re.search(regex, html)
        if match:
            return json.loads(match.group(1))
        _LOGGER.warning("failed to find device list\n\n{}", html)
        raise Exception("Cannot find device list!")

    async def _async_update_data(self):
        pass

    async def _async_setup(self):
        # works after hass version 2024.8
        html = await self.hass.async_add_executor_job(self.credentials.main_home_html)
        self.device_list = self.find_device_list_from_html(html)
        await self.hass.async_add_executor_job(self.fix_heat_datas)

        self.hass.bus.async_listen(
            "daelim_websocket_token_expired", self.websocket_token_expired
        )

        websocket_keys = await self.hass.async_add_executor_job(
            self.credentials.websocket_keys_json
        )
        self.hass.async_create_background_task(
            self._connect_websocket(websocket_keys), "daelim-websocket"
        )

    def fix_heat_datas(self):
        for devices in self.device_list:
            if devices["type"] != "heat":
                continue
            for device in devices["devices"]:
                if device["operation"]:
                    continue
                resp = self.request_device_status(device["uid"], "heat")
                if resp["result"]:
                    device["operation"] = resp["data"]

    async def _connect_websocket(self, websocket_keys):
        """Establish WebSocket connection."""
        url = "wss://smartelife.apt.co.kr/ws/data"
        data = websocket_keys | {
            "data": [
                {"type": "light"},
                {"type": "heat"},
                {"type": "alloffswitch"},
                {"type": "smartdoor"},
                {"type": "aircon"},
                # {"type": "call"},
            ]
        }
        json_data = json.dumps(data)

        while True:
            try:
                async with connect(url) as websocket:
                    await websocket.send(json_data)
                    while True:
                        message = await websocket.recv()
                        message = json.loads(message)
                        should_exit = await self.handle_websocket_message(message)
                        if should_exit:
                            break

            except websockets.exceptions.ConnectionClosed:
                # restart connection
                pass
            except TimeoutError:
                pass
            except ssl.SSLError:
                pass

    async def websocket_token_expired(self, _event_data):
        websocket_keys = await self.hass.async_add_executor_job(
            self.credentials.websocket_keys_json, True
        )
        self.hass.async_create_background_task(
            self._connect_websocket(websocket_keys), "daelim-websocket"
        )

    async def handle_websocket_message(self, message) -> bool:
        """Handle incoming WebSocket messages. Return true to exit loop"""

        has_expire_msg = (
            "result" in message
            and message["result"]["message"] == MESSAGE_WEBSOCKET_TOKEN_EXPIRED
        )
        if has_expire_msg:
            self.hass.bus.fire("daelim_websocket_token_expired")
            return True

        if "header" in message:
            # this is a response, ignore it
            pass
        elif "action" in message:
            _LOGGER.debug("Received websocket message: %s", message)
            self.async_set_updated_data(message)
        return False
