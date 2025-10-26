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
from homeassistant.util.ssl import get_default_context
from homeassistant.components import persistent_notification

from .const import DOMAIN
from .helper import request_ajax, get_html, Credentials

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.LIGHT,
    Platform.SWITCH,
    Platform.BUTTON,
]


MESSAGE_LOGGED_OUT = "장시간 미사용으로 로그아웃 되었습니다."
MESSAGE_WEBSOCKET_TOKEN_EXPIRED = "만료된 클라우드토큰 입니다."
MESSAGE_WEBSOCKET_STATUS_NORMAL = "정상"


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
        self.ssl_context = get_default_context()
        self.websocket_keys = None

    def request_device_status(self, device_uid, device_type):
        return self.request_ajax(
            "/controls/device/status.ajax", {"uid": device_uid, "type": device_type}
        )

    def request_ajax(self, url, json_data):
        daelim_header = self.credentials.daelim_header()
        return request_ajax(url, daelim_header, json_data)

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

    def find_elevator_uid(self, html):
        # data: JSON.stringify({
        # "header": {
        #     "category": "elevator",
        #     "type": "call",
        #     "command": "control_request"
        # },
        # "data" : {
        #     "uid": "CMF990100",
        #     "operation": {
        #         "control": "down"
        #     }
        # },
        regex = r'"category": "elevator",\s+"type": "call",\s+"command": "control_request"\s+},\s+"data" : {\s+"uid": "([^"]+)"'
        match = re.search(regex, html)
        if match:
            return match.group(1)
        _LOGGER.warning("failed to find elevator uid\n\n{}", html)
        return None

    async def _async_update_data(self):
        pass

    async def _async_setup(self):
        # works after hass version 2024.8
        html = await self.hass.async_add_executor_job(
            self.credentials.main_home_html, True
        )
        self.device_list = self.find_device_list_from_html(html)
        elevator_uid = self.find_elevator_uid(html)
        if elevator_uid:
            self.device_list.append(
                {
                    "type": "elevator",
                    "devices": [
                        {
                            "uid": elevator_uid,
                            "group": "Elevator",
                        }
                    ],
                }
            )

        await self.hass.async_add_executor_job(self.fix_heat_datas)

        self.hass.bus.async_listen(
            "daelim_websocket_token_expired", self.websocket_token_expired
        )

        self.websocket_keys = await self.hass.async_add_executor_job(
            self.credentials.websocket_keys_json, True
        )

        car_data = await self.hass.async_add_executor_job(self.get_car_data)
        if car_data:
            self.device_list.append(
                {
                    "type": "car",
                    "devices": car_data,
                }
            )

        self.hass.async_create_background_task(
            self._connect_websocket(), "daelim-websocket"
        )

    def get_car_data(self):
        url = "/monitoring/locationList.ajax"
        body = {
            "header": {
                "category": "board",
                "type": "location_list",
                "command": "query_request",
            },
            "data": {
                "roomkey": self.websocket_keys["roomKey"],
                "userkey": self.websocket_keys["userKey"],
                "location_type": "car",
            },
        }

        resp = self.request_ajax(url, body)

        if resp["result"]["status"] != "000":
            _LOGGER.warning("failed to get car data: %s", resp)
            return None
        return resp["data"]["list"]

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

    def send_notification(self, title, message, notification_id=None):
        """Send a notification to the user."""
        persistent_notification.async_create(
            self.hass,
            message,
            title=title,
            notification_id=notification_id if notification_id else "daelim_smarthome",
        )

    async def _connect_websocket(self):
        """Establish WebSocket connection."""
        url = "wss://smartelife.apt.co.kr/ws/data"
        data = self.websocket_keys | {
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
                async with connect(url, ssl=self.ssl_context) as websocket:
                    await websocket.send(json_data)
                    while True:
                        message = await websocket.recv()
                        message = json.loads(message)
                        should_exit = await self.handle_websocket_message(message)
                        if should_exit:
                            return

            except websockets.exceptions.ConnectionClosed:
                # restart connection
                _LOGGER.debug("WebSocket connection closed, reconnecting...")
                pass
            except TimeoutError:
                _LOGGER.debug("WebSocket connection timed out, reconnecting...")
                pass
            except ssl.SSLError:
                _LOGGER.error("SSL error occurred, reconnecting...")
                pass

    async def websocket_token_expired(self, event_data):
        # send notification with current date and time
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.send_notification(
            "Daelim WebSocket Token Expired",
            f"The WebSocket token has expired at {now}. Last message: {event_data}, Reconnecting in 1 minutes.",
            "daelim_websocket_token_expired",
        )
        await asyncio.sleep(60)  # wait for 1 minutes before reconnecting

        self.websocket_keys = await self.hass.async_add_executor_job(
            self.credentials.websocket_keys_json, True
        )
        self.hass.async_create_background_task(
            self._connect_websocket(self.websocket_keys), "daelim-websocket"
        )

    async def handle_websocket_message(self, message) -> bool:
        """Handle incoming WebSocket messages. Return true to exit loop"""

        has_normal_msg = (
            "result" in message
            and message["result"]["message"] == MESSAGE_WEBSOCKET_STATUS_NORMAL
        ) or "action" in message

        if not has_normal_msg:
            _LOGGER.debug("Received websocket message: %s", message)
            self.hass.bus.fire("daelim_websocket_token_expired", event_data=message)
            return True

        if "data" in message:
            processed_message = {}
            _LOGGER.debug("websocket message data: %s", message["data"])
            devices = message["data"].get("devices", [])
            for device in devices:
                processed_message[device["uid"]] = device.get("operation", {})
            self.async_set_updated_data(processed_message)

        return False
