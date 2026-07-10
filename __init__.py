"""The daelim-smarthome integration."""

from __future__ import annotations
from datetime import timedelta
from websockets.asyncio.client import connect
import logging
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


def is_logged_out(response) -> bool:
    """Whether an ajax response says the server session is gone."""
    if not isinstance(response, dict):
        return False
    result = response.get("result")
    if not isinstance(result, dict):
        return False
    return result.get("message") == MESSAGE_LOGGED_OUT


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
            # Periodic polling doubles as a keepalive: it exercises the
            # login state before a user action has to, so the server
            # never gets a chance to idle the session out.
            update_interval=timedelta(minutes=5),
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
        response = request_ajax(url, self.credentials.daelim_header(), json_data)
        if is_logged_out(response):
            _LOGGER.info("server dropped the session, logging in again")
            self.credentials.force_login()
            response = request_ajax(url, self.credentials.daelim_header(), json_data)
        return response

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
        car_data = await self.hass.async_add_executor_job(self.get_car_data)
        if car_data is not None:
            return {"car": car_data}
        return dict()

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

        # the html fetched above is cached, no need to force refresh
        self.websocket_keys = await self.hass.async_add_executor_job(
            self.credentials.websocket_keys_json
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
        _LOGGER.debug("got car data: %s", resp)
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
        """Keep the push connection alive for the lifetime of the entry.

        This task must never die: whatever goes wrong (network blip,
        expired cloud token, server hiccup), we back off and connect
        again. Expired keys are refreshed in-line, so there is no
        second task or event to get lost.
        """
        url = "wss://smartelife.apt.co.kr/ws/data"
        retry_delay = 5

        while True:
            try:
                # Refetch keys each iteration: a no-op while they are
                # still tied to the current login session, a cheap
                # refresh when a re-login elsewhere invalidated them.
                self.websocket_keys = await self.hass.async_add_executor_job(
                    self.credentials.websocket_keys_json
                )
                subscription = json.dumps(
                    self.websocket_keys
                    | {
                        "data": [
                            {"type": "light"},
                            {"type": "heat"},
                            {"type": "alloffswitch"},
                            {"type": "smartdoor"},
                            {"type": "aircon"},
                            # {"type": "call"},
                        ]
                    }
                )

                async with connect(url, ssl=self.ssl_context) as websocket:
                    retry_delay = 5  # reset after a successful connection
                    await websocket.send(subscription)
                    async for raw_message in websocket:
                        message = json.loads(raw_message)
                        if not self.handle_websocket_message(message):
                            # keys rejected: refresh and resubscribe
                            await self.refresh_websocket_keys(message)
                            break

            except websockets.exceptions.ConnectionClosed:
                _LOGGER.debug(
                    "WebSocket connection closed, reconnecting in %ss...", retry_delay
                )
            except (
                OSError,
                TimeoutError,
                ssl.SSLError,
                websockets.exceptions.WebSocketException,
            ) as err:
                _LOGGER.warning(
                    "WebSocket error (%s), reconnecting in %ss...", err, retry_delay
                )
            except Exception:
                _LOGGER.exception(
                    "Unexpected error in websocket task, reconnecting in %ss...",
                    retry_delay,
                )

            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 300)  # exponential backoff, max 5 min

    async def refresh_websocket_keys(self, message):
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.send_notification(
            "Daelim WebSocket Token Expired",
            f"The WebSocket token has expired at {now}. Last message: {message}. Refreshing keys and reconnecting.",
            "daelim_websocket_token_expired",
        )
        # Refresh keys within the current login session. Never force a
        # re-login here: a new login invalidates the other requests'
        # session, which would ping-pong invalidations between the
        # websocket and the control requests.
        self.websocket_keys = await self.hass.async_add_executor_job(
            self.credentials.websocket_keys_json, True
        )

    def handle_websocket_message(self, message) -> bool:
        """Handle an incoming WebSocket message.

        Return False when the server rejected our keys (anything but a
        normal status), signalling the caller to refresh and reconnect.
        """
        has_normal_msg = (
            "result" in message
            and message["result"]["message"] == MESSAGE_WEBSOCKET_STATUS_NORMAL
        ) or "action" in message

        if not has_normal_msg:
            _LOGGER.debug("Received websocket message: %s", message)
            return False

        if "data" in message:
            processed_message = {}
            _LOGGER.debug("websocket message data: %s", message["data"])
            devices = message["data"].get("devices", [])
            for device in devices:
                processed_message[device["uid"]] = device.get("operation", {})
            self.async_set_updated_data(processed_message)

        return True
