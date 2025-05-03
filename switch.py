"""Platform for switch integration."""

from __future__ import annotations

import logging
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
            entities = [
                DaelimAllOffSwitch(device_data, coordinator)
                for device_data in devices["devices"]
            ]

    async_add_entities(entities)


class DaelimAllOffSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of an Daelim switch."""

    def __init__(self, device_data, coordinator) -> None:
        """Initialize an Daelim all-off switch."""
        self.uid = device_data["uid"]
        super().__init__(coordinator, context=self.uid)
        self.coordinator = coordinator

        self.entity_id = "switch." + self.uid
        self._name = "All off switch"

        self._state = device_data["operation"]["status"] == "on"
        self._group = get_location(device_data)
        self._type = device_data["operation"]["type"]

    @property
    def name(self) -> str:
        """Return the display name of this switch."""
        return self._name

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

    def turn_on(self, **kwargs: Any) -> None:
        """Instruct the switch to turn on."""
        body = {
            "type": self._type,
            "uid": self.uid,
            "control": "on",
            "is_control_all": "N",
        }

        _response = self.coordinator.request_ajax("/device/control/all.ajax", body)
        self._state = True
        self.schedule_update_ha_state()

    def turn_off(self, **kwargs: Any) -> None:
        """Instruct the switch to turn off."""
        body = {
            "type": self._type,
            "uid": self.uid,
            "control": "off",
            "is_control_all": "N",
        }

        _response = self.coordinator.request_ajax("/device/control/all.ajax", body)
        self._state = False
        self.schedule_update_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        data = self.coordinator.data
        if (
            data["action"] == "event_alloffswitch"
            and data["data"]["devices"][0]["uid"] == self.uid
        ):
            self._state = data["data"]["devices"][0]["operation"]["status"] == "on"
        self.async_write_ha_state()
