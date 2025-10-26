"""Platform for button integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
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
        if devices["type"] == "elevator":
            entities = [
                DaelimElevatorCallButton(device_data, coordinator)
                for device_data in devices["devices"]
            ]

    async_add_entities(entities)


class DaelimElevatorCallButton(CoordinatorEntity, ButtonEntity):
    """Representation of an Daelim Elevator Call Button."""

    def __init__(self, device_data, coordinator) -> None:
        """Initialize an Daelim Elevator Call Button."""
        self.uid = device_data["uid"]
        super().__init__(coordinator, context=self.uid)
        self.coordinator = coordinator

        self.entity_id = "button." + self.uid
        self._name = "Call Elevator"
        self._group = "Elevator"

    @property
    def name(self) -> str:
        """Return the display name of this switch."""
        return self._name

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

    def press(self) -> None:
        """Handle the button press."""
        body = {
            "header": {
                "category": "elevator",
                "type": "call",
                "command": "control_request",
            },
            "data": {"uid": self.uid, "operation": {"control": "down"}},
        }
        _response = self.coordinator.request_ajax("/common/data.ajax", body)
