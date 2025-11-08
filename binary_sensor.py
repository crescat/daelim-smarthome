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
from dateutil import parser as dateparser
from datetime import timedelta

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(minutes=5)


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
        if devices["type"] == "car":
            entities += [
                DaelimCarSensor(device_data, coordinator)
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
        if self.uid in data:
            self._attr_is_on = data[self.uid]["status"] == "open"
            self.async_write_ha_state()


class DaelimCarSensor(CoordinatorEntity, BinarySensorEntity):
    """Representation of a Daelim Car Sensor."""

    def __init__(self, device_data, coordinator) -> None:
        """Initialize an DaelimCarSensor."""
        self.uid = "".join(
            c if c.isdigit() else f"-{ord(c)}-" for c in device_data["tag_num"]
        )
        print("setting uop car sensor", self.uid)
        super().__init__(coordinator, context=self.uid)
        self.coordinator = coordinator

        self.entity_id = "binary_sensor.car_" + self.uid
        self.car_number = device_data["tag_num"]
        self._attr_name = "Car " + self.car_number
        self._group = "car"

        self._attr_device_class = BinarySensorDeviceClass.PRESENCE
        location_text = device_data.get("location_text")
        self._attr_is_on = location_text is not None and location_text != ""
        date_str = device_data.get("datetime")

        self._attr_extra_state_attributes = {
            "location": location_text,
            "parked_since": dateparser.parse(date_str) if date_str else None,
        }

    @property
    def unique_id(self) -> str:
        """Return a unique, Home Assistant friendly identifier for this entity."""
        return self.uid

    @property
    def should_poll(self) -> bool:
        return True

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

        if "car" in data:
            for car_data in data["car"]:
                if car_data["tag_num"] == self.car_number:
                    location_text = car_data.get("location_text")
                    self._attr_is_on = location_text is not None and location_text != ""
                    date_str = car_data.get("datetime")
                    self._attr_extra_state_attributes.update(
                        {
                            "location": location_text,
                            "parked_since": dateparser.parse(date_str)
                            if date_str
                            else None,
                        }
                    )
                    self.async_write_ha_state()
                    return
            self._attr_is_on = False
            self.async_write_ha_state()
