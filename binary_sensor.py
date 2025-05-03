"""Platform for binary_sensor integration."""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)

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
    """Setup sensors"""
    coordinator = hass.data[DOMAIN]
    entities = []
    for devices in coordinator.device_list:
        if devices["type"] == "smartdoor":
            entities += [
                DaelimDoorSensor(device_data, coordinator)
                for device_data in devices["devices"]
            ]

    async_add_entities(entities)


class DaelimDoorSensor(CoordinatorEntity, BinarySensorEntity):
    """Representation of a Daelim Door Sensor."""

    def __init__(self, device_data, coordinator) -> None:
        """Initialize an DaelimDoorSensor."""
        self.uid = device_data["uid"]
        super().__init__(coordinator, context=self.uid)
        self.coordinator = coordinator

        self.entity_id = "binary_sensor.door_" + self.uid
        self._attr_name = "DoorLock"
        self._group = get_location(device_data)

        self._attr_device_class = BinarySensorDeviceClass.DOOR
        self._attr_is_on = device_data["operation"]["status"] == "open"
        self._attr_extra_state_attributes = {
            "battery": int(device_data["operation"]["battery"]),
            "low_battery": "n",
        }

    @property
    def unique_id(self) -> str:
        """Return a unique, Home Assistant friendly identifier for this entity."""
        return self.uid

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._group)},
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        data = self.coordinator.data
        if (
            data["action"] == "event_smartdoor"
            and data["data"]["devices"][0]["uid"] == self.uid
        ):
            self._attr_is_on = (
                data["data"]["devices"][0]["operation"]["status"] == "open"
            )
        self.async_write_ha_state()
