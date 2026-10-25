"""Config-Flow (Verbindung und Ordner) und Untereinträge (Spielpartner und Orte)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import logic
from .const import (
    CONF_ANNOUNCE_END,
    CONF_ANNOUNCE_ENTITY,
    CONF_ANNOUNCE_START,
    CONF_BASE_FOLDER,
    CONF_CAMS,
    CONF_COVER,
    CONF_COVER_POSITION,
    CONF_END_OFF,
    CONF_END_ON,
    CONF_END_SECONDS,
    CONF_FILE_PATTERN,
    CONF_LEAD_SECONDS,
    CONF_NUMBER,
    CONF_PARTNER_PATTERN,
    CONF_PREPARE_ON,
    CONF_PTZ_PRESET,
    CONF_RECORDER_KEY,
    CONF_RECORDER_URL,
    CONF_SEASON_PATTERN,
    CONF_START_OFF,
    CONF_START_ON,
    DEFAULT_BASE_FOLDER,
    DEFAULT_COVER_POSITION,
    DEFAULT_END_SECONDS,
    DEFAULT_FILE_PATTERN,
    DEFAULT_LEAD_SECONDS,
    DEFAULT_PARTNER_PATTERN,
    DEFAULT_RECORDER_URL,
    DEFAULT_SEASON_PATTERN,
    DOMAIN,
    SUBENTRY_PARTNER,
    SUBENTRY_PLACE,
)
from .recorder_client import RecorderAuthError, RecorderClient, RecorderError

_TEXT = selector.TextSelector()
_SECRET = selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD))
_TARGET = selector.TargetSelector()

HUB_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_RECORDER_URL, default=DEFAULT_RECORDER_URL): _TEXT,
        vol.Required(CONF_RECORDER_KEY): _SECRET,
        vol.Optional(CONF_BASE_FOLDER, default=DEFAULT_BASE_FOLDER): _TEXT,
        vol.Optional(CONF_PARTNER_PATTERN, default=DEFAULT_PARTNER_PATTERN): _TEXT,
        vol.Optional(CONF_SEASON_PATTERN, default=DEFAULT_SEASON_PATTERN): _TEXT,
        vol.Optional(CONF_FILE_PATTERN, default=DEFAULT_FILE_PATTERN): _TEXT,
    }
)


def _num(low: int, high: int, unit: str | None = None) -> selector.NumberSelector:
    config = selector.NumberSelectorConfig(min=low, max=high, step=1, mode=selector.NumberSelectorMode.BOX)
    if unit:
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(config)


def _hub_data(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Verbindung und Ordner-Muster aus dem Formular. Leere Muster fallen auf die eingebauten zurück."""
    return {
        CONF_RECORDER_URL: user_input[CONF_RECORDER_URL].strip().rstrip("/"),
        CONF_RECORDER_KEY: user_input[CONF_RECORDER_KEY].strip(),
        CONF_BASE_FOLDER: "/".join(logic.split_path(user_input.get(CONF_BASE_FOLDER) or "")),
        CONF_PARTNER_PATTERN: (user_input.get(CONF_PARTNER_PATTERN) or "").strip() or DEFAULT_PARTNER_PATTERN,
        CONF_SEASON_PATTERN: (user_input.get(CONF_SEASON_PATTERN) or "").strip() or DEFAULT_SEASON_PATTERN,
        CONF_FILE_PATTERN: (user_input.get(CONF_FILE_PATTERN) or "").strip() or DEFAULT_FILE_PATTERN,
    }


async def _check_recorder(hass: HomeAssistant, url: str, key: str) -> str | None:
    """Fehlerschlüssel der Verbindungsprüfung, None wenn die App antwortet."""
    try:
        await RecorderClient(async_get_clientsession(hass), url, key).status()
    except RecorderAuthError:
        return "invalid_auth"
    except RecorderError:
        return "cannot_connect"
    return None


# ------------------------------------------------------------------ Verbindung
class SpielSaisonConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 1

    async def _connection_step(
        self, step_id: str, user_input: dict[str, Any] | None, current: Mapping[str, Any]
    ) -> tuple[dict[str, Any] | None, ConfigFlowResult | None]:
        """Formular der Verbindung. Gibt (Daten, None) bei Erfolg zurück, sonst (None, Formular)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _hub_data(user_input)
            if error := await _check_recorder(self.hass, data[CONF_RECORDER_URL], data[CONF_RECORDER_KEY]):
                errors["base"] = error
            else:
                return data, None
        form = self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(HUB_SCHEMA, user_input or dict(current)),
            errors=errors,
        )
        return None, form

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        data, form = await self._connection_step("user", user_input, {})
        if data is None:
            assert form is not None
            return form
        return self.async_create_entry(title="Spiel Saison", data=data)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        data, form = await self._connection_step("reconfigure", user_input, entry.data)
        if data is None:
            assert form is not None
            return form
        # neu geladen wird über den Update-Listener der Integration, ein zweites Neuladen wäre doppelt
        return self.async_update_and_abort(entry, data_updates=data)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            key = user_input[CONF_RECORDER_KEY].strip()
            if error := await _check_recorder(self.hass, entry.data[CONF_RECORDER_URL], key):
                errors["base"] = error
            else:
                return self.async_update_and_abort(entry, data_updates={CONF_RECORDER_KEY: key})
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_RECORDER_KEY): _SECRET}),
            errors=errors,
        )

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_PARTNER: PartnerSubentryFlow, SUBENTRY_PLACE: PlaceSubentryFlow}


# ----------------------------------------------------------------- Spielpartner
class PartnerSubentryFlow(ConfigSubentryFlow):
    """Spielpartner anlegen oder bearbeiten: Name, Nummer (für die Muster) und feste Kameras."""

    _own: str | None = None

    def _others(self) -> list[Mapping[str, Any]]:
        return [
            s.data
            for sid, s in self._get_entry().subentries.items()
            if s.subentry_type == SUBENTRY_PARTNER and sid != self._own
        ]

    def _next_number(self) -> int:
        taken = {int(d.get(CONF_NUMBER) or 0) for d in self._others()}
        return next(n for n in range(1, 100) if n not in taken)

    async def _step(
        self, step_id: str, user_input: dict[str, Any] | None, current: Mapping[str, Any]
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input[CONF_NAME].strip()
            number = int(user_input[CONF_NUMBER])
            cams = list(user_input.get(CONF_CAMS) or [])
            others = self._others()
            if not name or logic.norm(name) in {logic.norm(d[CONF_NAME]) for d in others}:
                errors[CONF_NAME] = "name_taken"
            if number in {int(d.get(CONF_NUMBER) or 0) for d in others}:
                errors[CONF_NUMBER] = "number_taken"
            if not cams:
                errors[CONF_CAMS] = "cams_required"
            if not errors:
                data = {CONF_NAME: name, CONF_NUMBER: number, CONF_CAMS: cams}
                unique_id = logic.norm(name)
                if self._own is None:
                    return self.async_create_entry(title=name, data=data, unique_id=unique_id)
                return self.async_update_and_abort(
                    self._get_entry(), self._get_reconfigure_subentry(), title=name, unique_id=unique_id, data=data
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): _TEXT,
                vol.Required(CONF_NUMBER): _num(1, 99),
                vol.Required(CONF_CAMS): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="camera", multiple=True)
                ),
            }
        )
        suggested = user_input or {CONF_NUMBER: self._next_number(), **current}
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        self._own = None
        return await self._step("user", user_input, {})

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        sub = self._get_reconfigure_subentry()
        self._own = sub.subentry_id
        return await self._step("reconfigure", user_input, dict(sub.data))


# ------------------------------------------------------------------------ Orte
class PlaceSubentryFlow(ConfigSubentryFlow):
    """Ort anlegen oder bearbeiten, in drei Schritten: Grundlagen, Vorbereiten und Beginn, Ende."""

    _basics: dict[str, Any]
    _begin: dict[str, Any]
    _own: str | None = None

    def _taken_names(self) -> set[str]:
        return {
            logic.norm(s.data[CONF_NAME])
            for sid, s in self._get_entry().subentries.items()
            if s.subentry_type == SUBENTRY_PLACE and sid != self._own
        }

    # -------------------------------------------------------------- Schritt 1
    async def _step_basics(
        self, step_id: str, user_input: dict[str, Any] | None, current: Mapping[str, Any]
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input[CONF_NAME].strip()
            if not name or logic.norm(name) in self._taken_names():
                errors[CONF_NAME] = "name_taken"
            else:
                self._basics = {
                    CONF_NAME: name,
                    CONF_PTZ_PRESET: int(user_input.get(CONF_PTZ_PRESET) or 0),
                    CONF_LEAD_SECONDS: int(user_input.get(CONF_LEAD_SECONDS) or 0),
                    CONF_END_SECONDS: int(user_input.get(CONF_END_SECONDS) or 0),
                    CONF_ANNOUNCE_START: (user_input.get(CONF_ANNOUNCE_START) or "").strip(),
                    CONF_ANNOUNCE_END: (user_input.get(CONF_ANNOUNCE_END) or "").strip(),
                }
                if user_input.get(CONF_ANNOUNCE_ENTITY):
                    self._basics[CONF_ANNOUNCE_ENTITY] = user_input[CONF_ANNOUNCE_ENTITY]
                return await self._step_begin(f"{step_id}_begin", None, current)
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): _TEXT,
                vol.Optional(CONF_PTZ_PRESET): _num(0, 64),
                vol.Optional(CONF_LEAD_SECONDS): _num(0, 600, "s"),
                vol.Optional(CONF_END_SECONDS): _num(0, 600, "s"),
                vol.Optional(CONF_ANNOUNCE_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="notify")
                ),
                vol.Optional(CONF_ANNOUNCE_START): _TEXT,
                vol.Optional(CONF_ANNOUNCE_END): _TEXT,
            }
        )
        suggested = user_input or {
            CONF_PTZ_PRESET: 0,
            CONF_LEAD_SECONDS: DEFAULT_LEAD_SECONDS,
            CONF_END_SECONDS: DEFAULT_END_SECONDS,
            **current,
        }
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
            last_step=False,  # Schaltfläche: Weiter
        )

    # -------------------------------------------------------------- Schritt 2
    async def _step_begin(
        self, step_id: str, user_input: dict[str, Any] | None, current: Mapping[str, Any]
    ) -> SubentryFlowResult:
        if user_input is not None:
            self._begin = {
                key: user_input[key] for key in (CONF_PREPARE_ON, CONF_START_OFF, CONF_START_ON) if user_input.get(key)
            }
            if user_input.get(CONF_COVER):
                self._begin[CONF_COVER] = user_input[CONF_COVER]
                self._begin[CONF_COVER_POSITION] = int(user_input.get(CONF_COVER_POSITION, DEFAULT_COVER_POSITION))
            return await self._step_end(step_id.replace("_begin", "") + "_end", None, current)
        schema = vol.Schema(
            {
                vol.Optional(CONF_PREPARE_ON): _TARGET,
                vol.Optional(CONF_COVER): selector.EntitySelector(selector.EntitySelectorConfig(domain="cover")),
                vol.Optional(CONF_COVER_POSITION): _num(0, 100, "%"),
                vol.Optional(CONF_START_OFF): _TARGET,
                vol.Optional(CONF_START_ON): _TARGET,
            }
        )
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                schema, {CONF_COVER_POSITION: DEFAULT_COVER_POSITION, **current}
            ),
            last_step=False,  # Schaltfläche: Weiter
        )

    # -------------------------------------------------------------- Schritt 3
    async def _step_end(
        self, step_id: str, user_input: dict[str, Any] | None, current: Mapping[str, Any]
    ) -> SubentryFlowResult:
        if user_input is not None:
            data = {
                **self._basics,
                **self._begin,
                **{k: user_input[k] for k in (CONF_END_OFF, CONF_END_ON) if user_input.get(k)},
            }
            unique_id = logic.norm(data[CONF_NAME])
            if self._own is None:
                return self.async_create_entry(title=data[CONF_NAME], data=data, unique_id=unique_id)
            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                title=data[CONF_NAME],
                unique_id=unique_id,
                data=data,
            )
        schema = vol.Schema({vol.Optional(CONF_END_OFF): _TARGET, vol.Optional(CONF_END_ON): _TARGET})
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(schema, current),
            last_step=True,  # Schaltfläche: Ort speichern
        )

    # ------------------------------------------------------------- Einstiege
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        self._own = None
        return await self._step_basics("user", user_input, {})

    async def async_step_user_begin(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._step_begin("user_begin", user_input, {})

    async def async_step_user_end(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._step_end("user_end", user_input, {})

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        sub = self._get_reconfigure_subentry()
        self._own = sub.subentry_id
        return await self._step_basics("reconfigure", user_input, dict(sub.data))

    async def async_step_reconfigure_begin(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._step_begin("reconfigure_begin", user_input, dict(self._get_reconfigure_subentry().data))

    async def async_step_reconfigure_end(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._step_end("reconfigure_end", user_input, dict(self._get_reconfigure_subentry().data))
