"""Platform for climate integration."""

from __future__ import annotations

import logging
from typing import Any
from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate import (
    HVACMode,
    ClimateEntityFeature,
    PRESET_NONE,
    PRESET_AWAY,
    FAN_LOW,
    FAN_MEDIUM,
    FAN_HIGH,
    FAN_AUTO,
)

from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature, PRECISION_WHOLE

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

HVAC_TO_STR = {
    HVACMode.COOL: "cool",
    HVACMode.DRY: "dehumi",
    HVACMode.AUTO: "auto",
    HVACMode.FAN_ONLY: "fan",
}

STR_TO_HVAC = {v: k for k, v in HVAC_TO_STR.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Setup heating systems and air conditioning"""
    coordinator = hass.data[DOMAIN]
    entities = []
    for devices in coordinator.device_list:
        if devices["type"] == "heat":
            entities = [
                DaelimHeating(device_data, coordinator)
                for device_data in devices["devices"]
            ]
        elif devices["type"] == "aircon":
            entities = [
                DaelimAC(device_data, coordinator) for device_data in devices["devices"]
            ]

    async_add_entities(entities)


class DaelimHeating(CoordinatorEntity, ClimateEntity):
    """Representation of an Daelim Heating System."""

    def __init__(self, device_data, coordinator) -> None:
        """Initialize an DaelimHeating."""
        self.uid = device_data["uid"]
        super().__init__(coordinator, context=self.uid)
        self.coordinator = coordinator

        self.entity_id = "climate.heating_" + self.uid
        self._name = "{} Heating System".format(device_data["location_name"])
        self._group = device_data["location_name"]
        self._type = device_data["operation"]["type"]
        self._current_temperature = int(device_data["operation"]["current_temp"])
        self._target_temperature = int(device_data["operation"]["set_temp"])

        self._temperature_unit = UnitOfTemperature.CELSIUS
        self._precision = PRECISION_WHOLE
        self._hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
        self._preset_modes = [PRESET_NONE, PRESET_AWAY]

        self._current_hvac_mode = (
            HVACMode.HEAT
            if device_data["operation"]["control"] == "on"
            else HVACMode.OFF
        )

        self._current_preset_mode = (
            PRESET_AWAY if device_data["operation"]["mode"] == "out" else PRESET_NONE
        )

        self._supported_features = (
            ClimateEntityFeature.TURN_OFF
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.PRESET_MODE
        )
        self._enable_turn_on_off_backwards_compatibility = False
        self._min_temp = 5
        self._max_temp = 40

    @property
    def name(self) -> str:
        """Return the display name of this heater."""
        return self._name

    @property
    def is_on(self) -> bool | None:
        """Return true if heating system is on."""
        return self._current_hvac_mode != HVACMode.OFF

    @property
    def unique_id(self) -> str:
        """Return a unique, Home Assistant friendly identifier for this entity."""
        return self.uid

    @property
    def current_temperature(self):
        """Return the sensor temperature."""
        return self._current_temperature

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._target_temperature

    @property
    def temperature_unit(self):
        """Return the temperature unit."""
        return self._temperature_unit

    @property
    def precision(self):
        """Return the temperature precision."""
        return self._precision

    @property
    def hvac_modes(self):
        """Return the available HVAC modes."""
        return self._hvac_modes

    @property
    def hvac_mode(self):
        """Return the current HVAC mode."""
        return self._current_hvac_mode

    @property
    def preset_modes(self):
        """Return preset modes."""
        return self._preset_modes

    @property
    def preset_mode(self):
        """Return preset modes."""
        return self._current_preset_mode

    @property
    def supported_features(self):
        """Return supported features."""
        return self._supported_features

    @property
    def max_temp(self):
        """Return maximum available temperature."""
        return self._max_temp

    @property
    def min_temp(self):
        """Return minimum available temperature."""
        return self._min_temp

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._group)},
        )

    def set_temperature(self, **kwargs: Any):
        """Set new target temperature."""
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp and self._target_temperature == int(temp):
            return
        if self._current_hvac_mode == HVACMode.OFF:
            self.turn_on()
        self.control_set_temperature(temp)

    def set_hvac_mode(self, hvac_mode):
        """Set new target hvac mode."""
        if self._current_hvac_mode == hvac_mode:
            return
        if hvac_mode == HVACMode.HEAT:
            self.turn_on()
        elif hvac_mode == HVACMode.OFF:
            self.turn_off()
        self._current_hvac_mode = hvac_mode

    def set_preset_mode(self, preset_mode):
        """Set new target preset mode."""
        if preset_mode == self._current_preset_mode:
            return
        if self._current_hvac_mode == HVACMode.OFF:
            self.turn_on()
        self.control_set_mode(preset_mode)

    def control_set_mode(self, preset_mode):
        body = {
            "type": self._type,
            "uid": self.uid,
            "operation": {"mode": "out" if preset_mode == PRESET_AWAY else "heat"},
        }

        response = self.coordinator.request_ajax("/device/control.ajax", body)
        if response["result"]:
            self._current_preset_mode = preset_mode

        self.schedule_update_ha_state()

    def control_set_temperature(self, temp):
        body = {
            "type": self._type,
            "uid": self.uid,
            "operation": {"set_temp": str(temp)},
        }

        response = self.coordinator.request_ajax("/device/control.ajax", body)
        if response["result"]:
            self._target_temperature = int(temp)

        self.schedule_update_ha_state()

    def turn_on(self, **kwargs: Any) -> None:
        """Instruct the heating system to turn on."""
        body = {"type": self._type, "uid": self.uid, "operation": {"control": "on"}}

        response = self.coordinator.request_ajax("/device/control.ajax", body)
        if response["result"]:
            self._current_hvac_mode = HVACMode.HEAT

        self.schedule_update_ha_state()

    def turn_off(self, **kwargs: Any) -> None:
        """Instruct the heating system to turn off."""
        body = {"type": self._type, "uid": self.uid, "operation": {"control": "off"}}

        response = self.coordinator.request_ajax("/device/control.ajax", body)
        if response["result"]:
            self._current_hvac_mode = HVACMode.OFF

        self.schedule_update_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        data = self.coordinator.data
        if (
            data["action"] == "event_heat"
            and data["data"]["devices"][0]["uid"] == self.uid
        ):
            operation = data["data"]["devices"][0]["operation"]
            self._current_hvac_mode = (
                HVACMode.HEAT if operation["control"] == "on" else HVACMode.OFF
            )
            self._current_preset_mode = (
                PRESET_AWAY if operation["mode"] == "out" else None
            )
            self._current_temperature = int(operation["current_temp"])
            self._target_temperature = int(operation["set_temp"])

        self.async_write_ha_state()


class DaelimAC(CoordinatorEntity, ClimateEntity):
    """Representation of an Daelim AC."""

    def __init__(self, device_data, coordinator) -> None:
        """Initialize an Daelim AC."""
        self.uid = device_data["uid"]
        super().__init__(coordinator, context=self.uid)
        self.coordinator = coordinator

        self.entity_id = "climate.AC_" + self.uid
        self._name = "{} AC".format(device_data["location_name"])
        self._group = device_data["location_name"]
        self._type = device_data["operation"]["type"]

        self._current_temperature = self.parse_temp(
            device_data["operation"]["current_temp"]
        )

        self._target_temperature = self.parse_temp(device_data["operation"]["set_temp"])

        self._temperature_unit = UnitOfTemperature.CELSIUS
        self._precision = PRECISION_WHOLE
        self._hvac_modes = [
            HVACMode.OFF,
            HVACMode.COOL,
            HVACMode.DRY,
            HVACMode.AUTO,
            HVACMode.FAN_ONLY,
        ]

        self._current_hvac_mode = (
            HVACMode.OFF
            if device_data["operation"]["status"] == "off"
            else STR_TO_HVAC[device_data["operation"]["mode"]]
        )

        fan_speed = device_data["operation"]["wind_speed"]
        self._current_fan_mode = fan_speed if fan_speed else None
        self._fan_modes = [FAN_LOW, FAN_MEDIUM, FAN_HIGH, FAN_AUTO]

        self._supported_features = (
            ClimateEntityFeature.TURN_OFF
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.FAN_MODE
        )
        self._enable_turn_on_off_backwards_compatibility = False
        self._min_temp = 18
        self._max_temp = 30

    @property
    def name(self) -> str:
        """Return the display name of this AC."""
        return self._name

    @property
    def is_on(self) -> bool | None:
        """Return true if heating system is on."""
        return self._current_hvac_mode != HVACMode.OFF

    @property
    def unique_id(self) -> str:
        """Return a unique, Home Assistant friendly identifier for this entity."""
        return self.uid

    @property
    def current_temperature(self):
        """Return the sensor temperature."""
        return self._current_temperature

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._target_temperature

    @property
    def temperature_unit(self):
        """Return the temperature unit."""
        return self._temperature_unit

    @property
    def precision(self):
        """Return the temperature precision."""
        return self._precision

    @property
    def hvac_modes(self):
        """Return the available HVAC modes."""
        return self._hvac_modes

    @property
    def hvac_mode(self):
        """Return the current HVAC mode."""
        return self._current_hvac_mode

    @property
    def fan_modes(self):
        """Return the available fan modes."""
        return self._fan_modes

    @property
    def fan_mode(self):
        """Return the current fan mode."""
        return self._current_fan_mode

    @property
    def supported_features(self):
        """Return supported features."""
        return self._supported_features

    @property
    def max_temp(self):
        """Return maximum available temperature."""
        return self._max_temp

    @property
    def min_temp(self):
        """Return minimum available temperature."""
        return self._min_temp

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._group)},
        )

    def parse_temp(self, temp):
        temp = int(temp)
        if temp in [-1, 255]:
            return None
        return temp

    def set_temperature(self, **kwargs: Any):
        """Set new target temperature."""
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp and self._target_temperature == int(temp):
            return
        if self._current_hvac_mode == HVACMode.OFF:
            self.turn_on()
        self.control_set_temperature(temp)

    def set_hvac_mode(self, hvac_mode):
        """Set new target hvac mode."""
        if self._current_hvac_mode == hvac_mode:
            return
        if hvac_mode == HVACMode.OFF:
            self.turn_off()
            return

        if self._current_hvac_mode == HVACMode.OFF:
            self.turn_on()
        self.control_set_mode(hvac_mode)

    def set_fan_mode(self, fan_mode):
        """Set new target fan mode."""
        if self._current_fan_mode == fan_mode:
            return
        if self._current_hvac_mode == HVACMode.OFF:
            self.turn_on()
        self.control_set_fan(fan_mode)

    def control_set_mode(self, mode):
        body = {
            "type": self._type,
            "uid": self.uid,
            "operation": {"mode": HVAC_TO_STR[mode]},
        }

        response = self.coordinator.request_ajax("/device/control.ajax", body)
        if response["result"]:
            self._current_hvac_mode = mode

        self.schedule_update_ha_state()

    def control_set_fan(self, fan_mode):
        body = {
            "type": self._type,
            "uid": self.uid,
            "operation": {"wind_speed": fan_mode},
        }

        response = self.coordinator.request_ajax("/device/control.ajax", body)
        if response["result"]:
            self._current_fan_mode = fan_mode

        self.schedule_update_ha_state()

    def control_set_temperature(self, temp):
        body = {
            "type": self._type,
            "uid": self.uid,
            "operation": {"set_temp": str(temp)},
        }

        response = self.coordinator.request_ajax("/device/control.ajax", body)
        if response["result"]:
            self._target_temperature = int(temp)

        self.schedule_update_ha_state()

    def turn_on(self, **kwargs: Any) -> None:
        """Instruct the AC system to turn on."""
        body = {"type": self._type, "uid": self.uid, "operation": {"control": "on"}}

        response = self.coordinator.request_ajax("/device/control.ajax", body)
        if response["result"]:
            self._current_hvac_mode = HVACMode.AUTO

        self.schedule_update_ha_state()

    def turn_off(self, **kwargs: Any) -> None:
        """Instruct the AC system to turn off."""
        body = {"type": self._type, "uid": self.uid, "operation": {"control": "off"}}

        response = self.coordinator.request_ajax("/device/control.ajax", body)
        if response["result"]:
            self._current_hvac_mode = HVACMode.OFF

        self.schedule_update_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        data = self.coordinator.data
        if (
            data["action"] == "event_aircon"
            and data["data"]["devices"][0]["uid"] == self.uid
        ):
            operation = data["data"]["devices"][0]["operation"]
            self._current_hvac_mode = (
                HVACMode.OFF
                if operation["status"] == "off"
                else STR_TO_HVAC[operation["mode"]]
            )
            fan = operation["wind_speed"]
            self._current_fan_mode = fan if fan else None
            self._current_temperature = self.parse_temp(operation["current_temp"])
            self._target_temperature = self.parse_temp(operation["set_temp"])

        self.async_write_ha_state()
