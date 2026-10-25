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
from .cams import cam_name, ctronics_presets, preset_options
from .const import (
    CONF_ANNOUNCE_END,
    CONF_ANNOUNCE_ENTITY,
    CONF_ANNOUNCE_START,
    CONF_BASE_FOLDER,
    CONF_CAM_PRESETS,
    CONF_CAMS,
    CONF_COVER,
    CONF_COVER_POSITION,
    CONF_END_OFF,
    CONF_END_ON,
    CONF_END_SECONDS,
    CONF_FILE_PATTERN,
    CONF_FINISH_COVER,
    CONF_FINISH_COVER_POSITION,
    CONF_FINISH_OFF,
    CONF_FINISH_ON,
    CONF_LEAD_SECONDS,
    CONF_LIKE_CATALOG,
    CONF_LIKES,
    CONF_NICKNAME,
    CONF_NUMBER,
    CONF_PARTNER,
    CONF_PARTNER_PATTERN,
    CONF_PREPARE_ON,
    CONF_RECORDER_KEY,
    CONF_RECORDER_URL,
    CONF_SEASON_PATTERN,
    CONF_START_OFF,
    CONF_START_ON,
    CONF_TREAT_CATALOG,
    CONF_TREATS,
    DEFAULT_BASE_FOLDER,
    DEFAULT_COVER_POSITION,
    DEFAULT_END_SECONDS,
    DEFAULT_FILE_PATTERN,
    DEFAULT_FINISH_COVER_POSITION,
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
    MINOR_VERSION = 4

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
    """Spielpartner anlegen oder bearbeiten: Name, Nummer (für die Muster), Spitzname, Vorlieben und Ansage."""

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

    def _catalog(self, catalog_key: str, partner_key: str) -> list[str]:
        """Alle bekannten Vorlieben einer Art: die gemerkten und die der Spielpartner, nach Namen sortiert."""
        entry = self._get_entry()
        names = list(entry.data.get(catalog_key) or [])
        for sub in entry.subentries.values():
            if sub.subentry_type == SUBENTRY_PARTNER:
                names += sub.data.get(partner_key) or []
        return sorted(logic.clean_names(names), key=str.casefold)

    def _remember(self, data: Mapping[str, Any]) -> None:
        """Neue Vorlieben für die nächsten Spielpartner merken (im Eintrag, ohne eigene Einträge dafür)."""
        entry = self._get_entry()
        update: dict[str, list[str]] = {}
        for catalog_key, partner_key in ((CONF_LIKE_CATALOG, CONF_LIKES), (CONF_TREAT_CATALOG, CONF_TREATS)):
            stored = list(entry.data.get(catalog_key) or [])
            merged = logic.clean_names([*stored, *data[partner_key]], stored)
            if merged != stored:
                update[catalog_key] = merged
        if update:
            self.hass.config_entries.async_update_entry(entry, data={**entry.data, **update})

    async def _step(
        self, step_id: str, user_input: dict[str, Any] | None, current: Mapping[str, Any]
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        catalogs = {
            CONF_LIKES: self._catalog(CONF_LIKE_CATALOG, CONF_LIKES),
            CONF_TREATS: self._catalog(CONF_TREAT_CATALOG, CONF_TREATS),
        }
        if user_input is not None:
            name = user_input[CONF_NAME].strip()
            number = int(user_input[CONF_NUMBER])
            others = self._others()
            if not name or logic.norm(name) in {logic.norm(d[CONF_NAME]) for d in others}:
                errors[CONF_NAME] = "name_taken"
            if number in {int(d.get(CONF_NUMBER) or 0) for d in others}:
                errors[CONF_NUMBER] = "number_taken"
            if not errors:
                data: dict[str, Any] = {
                    CONF_NAME: name,
                    CONF_NUMBER: number,
                    CONF_NICKNAME: (user_input.get(CONF_NICKNAME) or "").strip(),
                    CONF_LIKES: logic.clean_names(user_input.get(CONF_LIKES) or [], catalogs[CONF_LIKES]),
                    CONF_TREATS: logic.clean_names(user_input.get(CONF_TREATS) or [], catalogs[CONF_TREATS]),
                    CONF_ANNOUNCE_START: (user_input.get(CONF_ANNOUNCE_START) or "").strip(),
                    CONF_ANNOUNCE_END: (user_input.get(CONF_ANNOUNCE_END) or "").strip(),
                }
                if user_input.get(CONF_ANNOUNCE_ENTITY):
                    data[CONF_ANNOUNCE_ENTITY] = user_input[CONF_ANNOUNCE_ENTITY]
                unique_id = logic.norm(name)
                self._remember(data)
                if self._own is None:
                    return self.async_create_entry(title=name, data=data, unique_id=unique_id)
                entry = self._get_entry()
                if name != current.get(CONF_NAME):  # die Titel der Orte nennen den Spielpartner
                    for sub in entry.subentries.values():
                        if sub.subentry_type == SUBENTRY_PLACE and sub.data.get(CONF_PARTNER) == self._own:
                            self.hass.config_entries.async_update_subentry(
                                entry, sub, title=logic.place_title(sub.data[CONF_NAME], name)
                            )
                return self.async_update_and_abort(
                    entry, self._get_reconfigure_subentry(), title=name, unique_id=unique_id, data=data
                )
        fields: dict[Any, Any] = {
            vol.Required(CONF_NAME): _TEXT,
            vol.Required(CONF_NUMBER): _num(1, 99),
            vol.Optional(CONF_NICKNAME): _TEXT,
        }
        for key, catalog in catalogs.items():  # eigene Eingaben sind erlaubt und stehen danach bei jedem zur Wahl
            fields[vol.Optional(key)] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[selector.SelectOptionDict(value=name, label=name) for name in catalog],
                    multiple=True,
                    custom_value=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        fields[vol.Optional(CONF_ANNOUNCE_ENTITY)] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="notify")
        )
        fields[vol.Optional(CONF_ANNOUNCE_START)] = _TEXT
        fields[vol.Optional(CONF_ANNOUNCE_END)] = _TEXT
        schema = vol.Schema(fields)
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
    """Ort anlegen oder bearbeiten.

    Schritte: Grundlagen, PTZ-Position je Kamera, Saison vorbereiten, Beginn, Ende, Saison beenden.
    „Saison vorbereiten“ und „Saison beenden“ gelten nur für die beiden Tasten gleichen Namens.
    """

    _basics: dict[str, Any]
    _prepare: dict[str, Any]
    _begin: dict[str, Any]
    _end: dict[str, Any]
    _own: str | None = None

    @property
    def _prefix(self) -> str:
        return "reconfigure" if self._own else "user"

    @property
    def _current(self) -> Mapping[str, Any]:
        return self._get_reconfigure_subentry().data if self._own else {}

    def _partners(self) -> dict[str, str]:
        """Subentry-ID -> Name der Spielpartner."""
        return {
            sid: s.data[CONF_NAME]
            for sid, s in self._get_entry().subentries.items()
            if s.subentry_type == SUBENTRY_PARTNER
        }

    def _taken(self) -> set[tuple[str, str]]:
        """Spielpartner und Name der anderen Orte: je Spielpartner darf ein Name nur einmal vorkommen."""
        return {
            (s.data.get(CONF_PARTNER, ""), logic.norm(s.data[CONF_NAME]))
            for sid, s in self._get_entry().subentries.items()
            if s.subentry_type == SUBENTRY_PLACE and sid != self._own
        }

    # -------------------------------------------------------------- Schritt 1
    async def _step_basics(self, user_input: dict[str, Any] | None) -> SubentryFlowResult:
        partners = self._partners()
        if not partners:
            return self.async_abort(reason="no_partner")
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input[CONF_NAME].strip()
            partner = user_input[CONF_PARTNER]
            cams = list(user_input.get(CONF_CAMS) or [])
            if not name or (partner, logic.norm(name)) in self._taken():
                errors[CONF_NAME] = "name_taken"
            if not cams:
                errors[CONF_CAMS] = "cams_required"
            if not errors:
                self._basics = {
                    CONF_NAME: name,
                    CONF_PARTNER: partner,
                    CONF_CAMS: cams,
                    CONF_CAM_PRESETS: {},
                    CONF_LEAD_SECONDS: int(user_input.get(CONF_LEAD_SECONDS) or 0),
                    CONF_END_SECONDS: int(user_input.get(CONF_END_SECONDS) or 0),
                }
                if any(ctronics_presets(self.hass, eid) is not None for eid in cams):
                    return await self._step_ptz(None)
                return await self._step_prepare(None)
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): _TEXT,
                vol.Required(CONF_PARTNER): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[selector.SelectOptionDict(value=sid, label=name) for sid, name in partners.items()],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_CAMS): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="camera", multiple=True)
                ),
                vol.Optional(CONF_LEAD_SECONDS): _num(0, 600, "s"),
                vol.Optional(CONF_END_SECONDS): _num(0, 600, "s"),
            }
        )
        suggested = user_input or {
            CONF_PARTNER: next(iter(partners)),
            CONF_LEAD_SECONDS: DEFAULT_LEAD_SECONDS,
            CONF_END_SECONDS: DEFAULT_END_SECONDS,
            **self._current,
        }
        return self.async_show_form(
            step_id=self._prefix,
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
            last_step=False,  # Schaltfläche: Weiter
        )

    # ----------------------------------------------- Schritt 2: PTZ je Kamera
    async def _step_ptz(self, user_input: dict[str, Any] | None) -> SubentryFlowResult:
        """Je Ctronics-Kamera ein Auswahlfeld mit ihren gespeicherten PTZ-Positionen."""
        cams = [eid for eid in self._basics[CONF_CAMS] if ctronics_presets(self.hass, eid) is not None]
        names = logic.unique_names([cam_name(self.hass, eid) for eid in cams], [eid.split(".", 1)[1] for eid in cams])
        fields = dict(zip(names, cams, strict=True))  # der Kamera-Name ist die Beschriftung des Feldes
        if user_input is not None:
            self._basics[CONF_CAM_PRESETS] = {
                eid: int(user_input[field]) for field, eid in fields.items() if int(user_input.get(field) or 0)
            }
            return await self._step_prepare(None)
        schema = vol.Schema(
            {
                vol.Optional(field): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=preset_options(self.hass, eid), mode=selector.SelectSelectorMode.DROPDOWN
                    )
                )
                for field, eid in fields.items()
            }
        )
        saved = self._current.get(CONF_CAM_PRESETS) or {}
        suggested = {field: str(saved.get(eid, 0)) for field, eid in fields.items()}
        return self.async_show_form(
            step_id=f"{self._prefix}_ptz",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            last_step=False,  # Schaltfläche: Weiter
        )

    # ------------------------------------------------ Schritt 3: Saison vorbereiten
    async def _step_prepare(self, user_input: dict[str, Any] | None) -> SubentryFlowResult:
        """Nur für die Taste „Saison vorbereiten“."""
        if user_input is not None:
            self._prepare = {CONF_PREPARE_ON: user_input[CONF_PREPARE_ON]} if user_input.get(CONF_PREPARE_ON) else {}
            if user_input.get(CONF_COVER):
                self._prepare[CONF_COVER] = user_input[CONF_COVER]
                self._prepare[CONF_COVER_POSITION] = int(user_input.get(CONF_COVER_POSITION, DEFAULT_COVER_POSITION))
            return await self._step_begin(None)
        schema = vol.Schema(
            {
                vol.Optional(CONF_PREPARE_ON): _TARGET,
                vol.Optional(CONF_COVER): selector.EntitySelector(selector.EntitySelectorConfig(domain="cover")),
                vol.Optional(CONF_COVER_POSITION): _num(0, 100, "%"),
            }
        )
        return self.async_show_form(
            step_id=f"{self._prefix}_prepare",
            data_schema=self.add_suggested_values_to_schema(
                schema, {CONF_COVER_POSITION: DEFAULT_COVER_POSITION, **self._current}
            ),
            last_step=False,  # Schaltfläche: Weiter
        )

    # -------------------------------------------------------------- Schritt 4: Beginn
    async def _step_begin(self, user_input: dict[str, Any] | None) -> SubentryFlowResult:
        if user_input is not None:
            self._begin = {key: user_input[key] for key in (CONF_START_OFF, CONF_START_ON) if user_input.get(key)}
            return await self._step_end(None)
        schema = vol.Schema({vol.Optional(CONF_START_OFF): _TARGET, vol.Optional(CONF_START_ON): _TARGET})
        return self.async_show_form(
            step_id=f"{self._prefix}_begin",
            data_schema=self.add_suggested_values_to_schema(schema, self._current),
            last_step=False,  # Schaltfläche: Weiter
        )

    # -------------------------------------------------------------- Schritt 5: Ende
    async def _step_end(self, user_input: dict[str, Any] | None) -> SubentryFlowResult:
        if user_input is not None:
            self._end = {key: user_input[key] for key in (CONF_END_OFF, CONF_END_ON) if user_input.get(key)}
            return await self._step_finish(None)
        schema = vol.Schema({vol.Optional(CONF_END_OFF): _TARGET, vol.Optional(CONF_END_ON): _TARGET})
        return self.async_show_form(
            step_id=f"{self._prefix}_end",
            data_schema=self.add_suggested_values_to_schema(schema, self._current),
            last_step=False,  # Schaltfläche: Weiter
        )

    # ------------------------------------------------- Schritt 6: Saison beenden
    async def _step_finish(self, user_input: dict[str, Any] | None) -> SubentryFlowResult:
        """Nur für die Taste „Saison beenden“."""
        if user_input is not None:
            finish = {key: user_input[key] for key in (CONF_FINISH_OFF, CONF_FINISH_ON) if user_input.get(key)}
            if user_input.get(CONF_FINISH_COVER):
                finish[CONF_FINISH_COVER] = user_input[CONF_FINISH_COVER]
                finish[CONF_FINISH_COVER_POSITION] = int(
                    user_input.get(CONF_FINISH_COVER_POSITION, DEFAULT_FINISH_COVER_POSITION)
                )
            data = {**self._basics, **self._prepare, **self._begin, **self._end, **finish}
            title = logic.place_title(data[CONF_NAME], self._partners()[data[CONF_PARTNER]])
            unique_id = f"{data[CONF_PARTNER]}:{logic.norm(data[CONF_NAME])}"
            if self._own is None:
                return self.async_create_entry(title=title, data=data, unique_id=unique_id)
            return self.async_update_and_abort(
                self._get_entry(), self._get_reconfigure_subentry(), title=title, unique_id=unique_id, data=data
            )
        schema = vol.Schema(
            {
                vol.Optional(CONF_FINISH_OFF): _TARGET,
                vol.Optional(CONF_FINISH_ON): _TARGET,
                vol.Optional(CONF_FINISH_COVER): selector.EntitySelector(selector.EntitySelectorConfig(domain="cover")),
                vol.Optional(CONF_FINISH_COVER_POSITION): _num(0, 100, "%"),
            }
        )
        return self.async_show_form(
            step_id=f"{self._prefix}_finish",
            data_schema=self.add_suggested_values_to_schema(
                schema, {CONF_FINISH_COVER_POSITION: DEFAULT_FINISH_COVER_POSITION, **self._current}
            ),
            last_step=True,  # Schaltfläche: Ort speichern
        )

    # ------------------------------------------------------------- Einstiege
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        self._own = None
        return await self._step_basics(user_input)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        self._own = self._get_reconfigure_subentry().subentry_id
        return await self._step_basics(user_input)

    async def async_step_user_ptz(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._step_ptz(user_input)

    async def async_step_reconfigure_ptz(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._step_ptz(user_input)

    async def async_step_user_prepare(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._step_prepare(user_input)

    async def async_step_reconfigure_prepare(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._step_prepare(user_input)

    async def async_step_user_begin(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._step_begin(user_input)

    async def async_step_reconfigure_begin(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._step_begin(user_input)

    async def async_step_user_end(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._step_end(user_input)

    async def async_step_reconfigure_end(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._step_end(user_input)

    async def async_step_user_finish(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._step_finish(user_input)

    async def async_step_reconfigure_finish(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._step_finish(user_input)
