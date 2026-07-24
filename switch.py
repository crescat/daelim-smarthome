"""Platform for switch integration."""

from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .helper import get_location
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_NAME_TOKENS = {"콘센트": "Outlet", "주방": "Kitchen"}


def englishize(name: str) -> str:
    for korean, english in _NAME_TOKENS.items():
        name = name.replace(korean, english + " ")
    return re.sub(r"\s+", " ", name).strip()


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Setup switchs"""
    coordinator = hass.data[DOMAIN]
    entities = []
    for devices in coordinator.device_list:
        if devices["type"] == "alloffswitch":
            entities += [
                DaelimAllOffSwitch(device_data, coordinator)
                for device_data in devices["devices"]
            ]
        elif devices["type"] == "wallsocket":
            entities += [
                DaelimWallSocket(device_data, coordinator)
                for device_data in devices["devices"]
            ]

    async_add_entities(entities)


class DaelimSwitch(CoordinatorEntity, SwitchEntity):
    """A Daelim on/off relay (all-off switch, standby-power outlet).

    Both are the same concept server-side: a device with an on/off status
    driven through /device/control/all.ajax. Subclasses only choose the name.
    """

    def __init__(self, device_data, coordinator) -> None:
        self.uid = device_data["uid"]
        super().__init__(coordinator, context=self.uid)
        self.coordinator = coordinator

        self._state = device_data["operation"]["status"] == "on"
        self._group = get_location(device_data)
        self._type = device_data["operation"]["type"]

    @property
    def is_on(self) -> bool | None:
        """Return true if switch is on."""
        return self._state

    @property
    def unique_id(self) -> str:
        """Return a unique, Home Assistant friendly identifier for this entity."""
        return self.uid

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._group)},
            name=self._group,
            manufacturer="Daelim Smarthome",
        )

    def _control(self, control: str) -> None:
        body = {
            "type": self._type,
            "uid": self.uid,
            "control": control,
            "is_control_all": "N",
        }
        self.coordinator.request_ajax("/device/control/all.ajax", body)

    def turn_on(self, **kwargs: Any) -> None:
        """Instruct the switch to turn on."""
        self._control("on")
        self._state = True
        self.schedule_update_ha_state()

    def turn_off(self, **kwargs: Any) -> None:
        """Instruct the switch to turn off."""
        self._control("off")
        self._state = False
        self.schedule_update_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        data = self.coordinator.data
        if self.uid in data:
            self._state = data[self.uid]["status"] == "on"
            self.async_write_ha_state()


class DaelimAllOffSwitch(DaelimSwitch):
    """The single whole-home all-off switch."""

    def __init__(self, device_data, coordinator) -> None:
        super().__init__(device_data, coordinator)
        self._attr_name = "All off switch"


class DaelimWallSocket(DaelimSwitch):
    """A standby-power outlet (대기전력 콘센트)."""

    def __init__(self, device_data, coordinator) -> None:
        super().__init__(device_data, coordinator)
        self._attr_name = "{} {}".format(
            get_location(device_data), englishize(device_data["device_name"])
        )
