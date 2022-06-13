"""Light support for switch entities."""
from __future__ import annotations

import logging
import math
from typing import Any

import voluptuous as vol

from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_NAME,
    EVENT_HOMEASSISTANT_START,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    TEMP_CELSIUS,
)
from homeassistant.components.water_heater import (
    SUPPORT_OPERATION_MODE,
    SUPPORT_TARGET_TEMPERATURE,
    WaterHeaterEntity,
    STATE_ELECTRIC,
)
from homeassistant.core import Event, HomeAssistant, callback, CoreState
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers import entity_registry as er
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.config_validation import PLATFORM_SCHEMA
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import SENSOR_DOMAIN, SWITCH_DOMAIN, ROOM_TEMP, BOIL_TEMP

CONF_SWITCH_ENTITY_ID = 'switch'
CONF_TEMPERATURE_ENTITY_ID = 'temperature'
CONF_MIN_TEMP = 'min_temperature'
CONF_MAX_TEMP = 'max_temperature'

DEFAULT_NAME = "Complex Water Heater"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Required(CONF_SWITCH_ENTITY_ID): cv.entity_domain(SWITCH_DOMAIN),
        vol.Required(CONF_TEMPERATURE_ENTITY_ID): cv.entity_domain(SENSOR_DOMAIN),
        vol.Optional(CONF_MIN_TEMP, default=ROOM_TEMP): cv.positive_int,
        vol.Optional(CONF_MAX_TEMP, default=BOIL_TEMP): cv.positive_int,
    }
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Initialize Light Switch platform."""
    registry = er.async_get(hass)
    wrapped_switch = registry.async_get(config[CONF_SWITCH_ENTITY_ID])
    unique_id = wrapped_switch.unique_id + '_water_heater' if wrapped_switch else None

    async_add_entities(
        [
            ComplexWaterHeater(
                config[CONF_NAME],
                config[CONF_SWITCH_ENTITY_ID],
                config[CONF_TEMPERATURE_ENTITY_ID],
                config[CONF_MIN_TEMP],
                config[CONF_MAX_TEMP],
                unique_id,
            )
        ]
    )


class ComplexWaterHeater(WaterHeaterEntity):
    """Represents a Switch as a Light."""

    _attr_should_poll = False
    _attr_supported_features = SUPPORT_OPERATION_MODE
    _attr_operation_list = [STATE_OFF, STATE_ELECTRIC]

    def __init__(self,
                 name: str,
                 switch_entity_id: str,
                 temperature_entity_id: str,
                 min_temp: float,
                 max_temp: float,
                 unique_id: str | None,
                 ) -> None:
        """Initialize Light Switch."""
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._attr_min_temp = min_temp
        self._attr_max_temp = max_temp
        self._switch_entity_id = switch_entity_id
        self._temperature_entity_id = temperature_entity_id

        self._cur_temp = None

    @property
    def min_temp(self):
        return ROOM_TEMP

    @property
    def max_temp(self):
        return BOIL_TEMP

    @property
    def temperature_unit(self):
        return TEMP_CELSIUS

    @property
    def current_temperature(self):
        """Return the sensor temperature."""
        return self._cur_temp

    @property
    def target_temperature(self):
        return self.max_temp if self._attr_is_on else self.min_temp

    @property
    def current_operation(self):
        return STATE_ELECTRIC if self._attr_is_on else STATE_OFF

    @property
    def icon(self):
        return "mdi:kettle"

    @property
    def should_poll(self):
        return False

    # @property
    # def unique_id(self):
    #     return self.entry.entry_id + "_water_heater"

    @callback
    def _async_update_temp(self, state):
        """Update thermostat with latest state from sensor."""
        try:
            cur_temp = float(state.state)
            if math.isnan(cur_temp) or math.isinf(cur_temp):
                raise ValueError(f"Sensor has illegal state {state.state}")
            self._cur_temp = cur_temp
        except ValueError as ex:
            _LOGGER.error("Unable to update from sensor: %s", ex)

    async def _async_sensor_changed(self, event):
        """Handle temperature changes."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        self._async_update_temp(new_state)
        # await self._async_control_heating()
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Forward the turn_on command to the switch in this light switch."""
        await self.hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: self._switch_entity_id},
            blocking=True,
            context=self._context,
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Forward the turn_off command to the switch in this light switch."""
        await self.hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: self._switch_entity_id},
            blocking=True,
            context=self._context,
        )

    async def async_set_operation_mode(self, operation_mode):
        """Set new operation mode."""
        if operation_mode == STATE_OFF:
            await self.async_turn_off()
        else:
            await self.async_turn_on()
        self.hass.async_add_executor_job(async_dispatcher_send, self.hass, 'update')

    async def async_added_to_hass(self) -> None:
        """Register callbacks."""

        # Add listener
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._temperature_entity_id], self._async_sensor_changed
            )
        )

        @callback
        def _async_startup(*_):
            """Init on startup."""
            sensor_state = self.hass.states.get(self._temperature_entity_id)
            if sensor_state and sensor_state.state not in (
                STATE_UNAVAILABLE,
                STATE_UNKNOWN,
            ):
                self._async_update_temp(sensor_state)
                self.async_write_ha_state()

        if self.hass.state == CoreState.running:
            _async_startup()
        else:
            self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_START, _async_startup)

        @callback
        def async_state_changed_listener(event: Event | None = None) -> None:
            """Handle child updates."""
            if (
                state := self.hass.states.get(self._switch_entity_id)
            ) is None or state.state == STATE_UNAVAILABLE:
                self._attr_available = False
                return
            self._attr_available = True
            self._attr_is_on = state.state == STATE_ON
            self.async_write_ha_state()

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._switch_entity_id], async_state_changed_listener
            )
        )
        # Call once on adding
        async_state_changed_listener()
