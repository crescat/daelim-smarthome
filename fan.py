"""Platform for fan integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature

from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .helper import get_location
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PRESET_MODES = ["manual", "auto", "cleaning", "bypass"]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Setup ventilation fans"""
    coordinator = hass.data[DOMAIN]
    entities = []
    for devices in coordinator.device_list:
        if devices["type"] == "vent":
            entities += [
                DaelimVent(device_data, coordinator)
                for device_data in devices["devices"]
            ]

    async_add_entities(entities)


class DaelimVent(CoordinatorEntity, FanEntity):
    """Representation of a Daelim ventilation fan (on/off + mode)."""

    _attr_preset_modes = PRESET_MODES

    def __init__(self, device_data, coordinator) -> None:
        """Initialize a DaelimVent."""
        self.uid = device_data["uid"]
        super().__init__(coordinator, context=self.uid)
        self.coordinator = coordinator

        operation = device_data["operation"]
        self._attr_name = "{} Ventilation".format(get_location(device_data))
        self._group = get_location(device_data)
        self._type = operation["type"]
        self._state = operation["status"] == "on"
        self._mode = operation.get("mode")

        self._attr_supported_features = (
            FanEntityFeature.PRESET_MODE
            | FanEntityFeature.TURN_ON
            | FanEntityFeature.TURN_OFF
        )
        self._enable_turn_on_off_backwards_compatibility = False

    @property
    def is_on(self) -> bool | None:
        """Return true if the fan is on."""
        return self._state

    @property
    def preset_mode(self) -> str | None:
        """Return the current ventilation mode."""
        return self._mode if self._mode in PRESET_MODES else None

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

    def _control(self, operation: dict) -> bool:
        body = {"type": self._type, "uid": self.uid, "operation": operation}
        response = self.coordinator.request_ajax("/device/control.ajax", body)
        return bool(response["result"])

    def turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn the fan on, optionally in a given mode."""
        if self._control({"control": "on", "off_rsv_time": "0"}):
            self._state = True
        if preset_mode is not None:
            self.set_preset_mode(preset_mode)
        else:
            self.schedule_update_ha_state()

    def turn_off(self, **kwargs: Any) -> None:
        """Turn the fan off."""
        if self._control({"control": "off", "off_rsv_time": "0"}):
            self._state = False
        self.schedule_update_ha_state()

    def set_preset_mode(self, preset_mode: str) -> None:
        """Set the ventilation mode."""
        if self._control({"mode": preset_mode}):
            self._mode = preset_mode
            self._state = True
        self.schedule_update_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        data = self.coordinator.data
        if self.uid in data:
            operation = data[self.uid]
            self._state = operation.get("status") == "on"
            mode = operation.get("mode")
            if mode:
                self._mode = mode
            self.async_write_ha_state()
