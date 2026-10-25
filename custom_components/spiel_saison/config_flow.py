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
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import logic
from .cams import cam_name, ctronics_presets, entity_title, preset_options
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
    CONF_LEAD_SECONDS,
    CONF_LIKE_CATALOG,
    CONF_LIKES,
    CONF_NICKNAME,
    CONF_NUMBER,
    CONF_PARTNER,
    CONF_PARTNER_PATTERN,
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
    DEFAULT_END_SWITCH_WAIT,
    DEFAULT_FILE_PATTERN,
    DEFAULT_FINISH_COVER_POSITION,
    DEFAULT_LEAD_SECONDS,
    DEFAULT_PARTNER_PATTERN,
    DEFAULT_RECORDER_URL,
    DEFAULT_SEASON_PATTERN,
    DOMAIN,
    LIGHT_BRIGHTNESS,
    LIGHT_RGB,
    OPT_FINISH,
    OPT_PREPARE,
    STEP_AFTER_OFF,
    STEP_COVER,
    STEP_COVER_POSITION,
    STEP_END_SWITCH,
    STEP_LIGHTS,
    STEP_OFF,
    STEP_ON,
    STEP_WAIT,
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
    MINOR_VERSION = 5

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

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return SeasonOptionsFlow()

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
    """Ort anlegen oder bearbeiten: Grundlagen, PTZ-Position je Kamera, Rollo und Winde, Beginn, Ende."""

    _basics: dict[str, Any]
    _cover: dict[str, Any]
    _begin: dict[str, Any]
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
                return await self._step_cover(None)
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
            return await self._step_cover(None)
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

    # -------------------------------------------------- Schritt 3: Rollo und Winde
    async def _step_cover(self, user_input: dict[str, Any] | None) -> SubentryFlowResult:
        """Optional: Rollo oder Winde dieses Ortes für „Saison vorbereiten“ und „Saison beenden“."""
        if user_input is not None:
            self._cover = {}
            for key, position_key, default in (
                (CONF_COVER, CONF_COVER_POSITION, DEFAULT_COVER_POSITION),
                (CONF_FINISH_COVER, CONF_FINISH_COVER_POSITION, DEFAULT_FINISH_COVER_POSITION),
            ):
                if user_input.get(key):
                    self._cover[key] = user_input[key]
                    self._cover[position_key] = int(user_input.get(position_key, default))
            return await self._step_begin(None)
        cover = selector.EntitySelector(selector.EntitySelectorConfig(domain="cover"))
        schema = vol.Schema(
            {
                vol.Optional(CONF_COVER): cover,
                vol.Optional(CONF_COVER_POSITION): _num(0, 100, "%"),
                vol.Optional(CONF_FINISH_COVER): cover,
                vol.Optional(CONF_FINISH_COVER_POSITION): _num(0, 100, "%"),
            }
        )
        suggested = {
            CONF_COVER_POSITION: DEFAULT_COVER_POSITION,
            CONF_FINISH_COVER_POSITION: DEFAULT_FINISH_COVER_POSITION,
            **self._current,
        }
        return self.async_show_form(
            step_id=f"{self._prefix}_cover",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            last_step=False,  # Schaltfläche: Weiter
        )

    # -------------------------------------------------------------- Schritt 4
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

    # -------------------------------------------------------------- Schritt 5
    async def _step_end(self, user_input: dict[str, Any] | None) -> SubentryFlowResult:
        if user_input is not None:
            data = {
                **self._basics,
                **self._cover,
                **self._begin,
                **{k: user_input[k] for k in (CONF_END_OFF, CONF_END_ON) if user_input.get(k)},
            }
            title = logic.place_title(data[CONF_NAME], self._partners()[data[CONF_PARTNER]])
            unique_id = f"{data[CONF_PARTNER]}:{logic.norm(data[CONF_NAME])}"
            if self._own is None:
                return self.async_create_entry(title=title, data=data, unique_id=unique_id)
            return self.async_update_and_abort(
                self._get_entry(), self._get_reconfigure_subentry(), title=title, unique_id=unique_id, data=data
            )
        schema = vol.Schema({vol.Optional(CONF_END_OFF): _TARGET, vol.Optional(CONF_END_ON): _TARGET})
        return self.async_show_form(
            step_id=f"{self._prefix}_end",
            data_schema=self.add_suggested_values_to_schema(schema, self._current),
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

    async def async_step_user_cover(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._step_cover(user_input)

    async def async_step_reconfigure_cover(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._step_cover(user_input)

    async def async_step_user_begin(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._step_begin(user_input)

    async def async_step_reconfigure_begin(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._step_begin(user_input)

    async def async_step_user_end(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._step_end(user_input)

    async def async_step_reconfigure_end(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._step_end(user_input)


# --------------------------------------------- Saison vorbereiten und Saison beenden
class SeasonOptionsFlow(OptionsFlow):
    """„Konfigurieren“ der Integration: was „Saison vorbereiten“ und „Saison beenden“ schalten.

    Beides gilt für alle Spielpartner und Orte und nur für die beiden Tasten (und Dienste), nicht für die Aufnahme.
    Seiten: Saison vorbereiten, deren Lichter, Saison beenden, deren Lichter. Die Seiten der Lichter gibt es nur,
    wenn Lichter gewählt sind.
    """

    def __init__(self) -> None:
        self._options: dict[str, dict[str, Any]] = {}

    def _saved(self, section: str) -> Mapping[str, Any]:
        return self.config_entry.options.get(section) or {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._step_section(OPT_PREPARE, user_input)

    async def async_step_prepare_lights(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._step_lights(OPT_PREPARE, user_input)

    async def async_step_finish(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._step_section(OPT_FINISH, user_input)

    async def async_step_finish_lights(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._step_lights(OPT_FINISH, user_input)

    async def _step_section(self, section: str, user_input: dict[str, Any] | None) -> ConfigFlowResult:
        prepare = section == OPT_PREPARE
        default_position = DEFAULT_COVER_POSITION if prepare else DEFAULT_FINISH_COVER_POSITION
        saved = self._saved(section)
        if user_input is not None:
            data: dict[str, Any] = {key: user_input[key] for key in (STEP_OFF, STEP_ON) if user_input.get(key)}
            if user_input.get(STEP_COVER):
                data[STEP_COVER] = user_input[STEP_COVER]
                data[STEP_COVER_POSITION] = int(user_input.get(STEP_COVER_POSITION, default_position))
            if not prepare:  # nur „Saison beenden“ wartet auf einen Endschalter
                if user_input.get(STEP_AFTER_OFF):
                    data[STEP_AFTER_OFF] = user_input[STEP_AFTER_OFF]
                if user_input.get(STEP_END_SWITCH):
                    data[STEP_END_SWITCH] = user_input[STEP_END_SWITCH]
                    data[STEP_WAIT] = int(user_input.get(STEP_WAIT, DEFAULT_END_SWITCH_WAIT))
            old = saved.get(STEP_LIGHTS) or {}
            lights = list(user_input.get(STEP_LIGHTS) or [])
            if lights:  # die bisherigen Farben bleiben, bis du sie auf der nächsten Seite änderst
                data[STEP_LIGHTS] = {entity_id: dict(old.get(entity_id) or {}) for entity_id in lights}
            self._options[section] = data
            return await (self._step_lights(section, None) if lights else self._next(section))
        fields: dict[Any, Any] = {
            vol.Optional(STEP_OFF): _TARGET,
            vol.Optional(STEP_ON): _TARGET,
            vol.Optional(STEP_LIGHTS): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="light", multiple=True)
            ),
            vol.Optional(STEP_COVER): selector.EntitySelector(selector.EntitySelectorConfig(domain="cover")),
            vol.Optional(STEP_COVER_POSITION): _num(0, 100, "%"),
        }
        suggested = {STEP_COVER_POSITION: default_position, **saved, STEP_LIGHTS: list(saved.get(STEP_LIGHTS) or {})}
        if not prepare:
            fields[vol.Optional(STEP_AFTER_OFF)] = _TARGET
            fields[vol.Optional(STEP_END_SWITCH)] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["binary_sensor", "switch", "input_boolean"])
            )
            fields[vol.Optional(STEP_WAIT)] = _num(1, 600, "s")
            suggested = {STEP_WAIT: DEFAULT_END_SWITCH_WAIT, **suggested}
        schema = vol.Schema(fields)
        return self.async_show_form(
            step_id="init" if prepare else "finish",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            last_step=False if prepare else None,  # „Weiter“, solange noch eine Seite folgt
        )

    async def _step_lights(self, section: str, user_input: dict[str, Any] | None) -> ConfigFlowResult:
        """Je Licht ein Farbwähler und die Helligkeit. Leer lassen heißt: das Licht geht so an, wie es zuletzt war."""
        prepare = section == OPT_PREPARE
        lights = self._options[section][STEP_LIGHTS]
        german = self.hass.config.language.startswith("de")
        color_word, brightness_word = ("Farbe", "Helligkeit") if german else ("Colour", "Brightness")
        ids = list(lights)
        names = logic.unique_names(
            [entity_title(self.hass, entity_id) for entity_id in ids], [entity_id.split(".", 1)[1] for entity_id in ids]
        )
        fields: dict[str, tuple[str, str]] = {}  # Beschriftung des Feldes -> (Licht, Farbe oder Helligkeit)
        for name, entity_id in zip(names, ids, strict=True):
            fields[f"{name} {color_word}"] = (entity_id, LIGHT_RGB)
            fields[f"{name} {brightness_word}"] = (entity_id, LIGHT_BRIGHTNESS)
        if user_input is not None:
            for label, (entity_id, kind) in fields.items():
                if value := user_input.get(label):
                    lights[entity_id][kind] = [int(part) for part in value] if kind == LIGHT_RGB else int(value)
                else:
                    lights[entity_id].pop(kind, None)
            return await self._next(section)
        schema = vol.Schema(
            {
                vol.Optional(label): selector.ColorRGBSelector() if kind == LIGHT_RGB else _num(1, 100, "%")
                for label, (_entity_id, kind) in fields.items()
            }
        )
        suggested = {
            label: lights[entity_id][kind] for label, (entity_id, kind) in fields.items() if kind in lights[entity_id]
        }
        return self.async_show_form(
            step_id=f"{section}_lights",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            last_step=False if prepare else None,
        )

    async def _next(self, section: str) -> ConfigFlowResult:
        if section == OPT_PREPARE:
            return await self.async_step_finish()
        return self.async_create_entry(data={**self.config_entry.options, **self._options})
