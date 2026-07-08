# pyright: reportUnknownParameterType=false, reportExplicitAny=false, reportUnknownMemberType=false

"""Integration config flow."""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Literal, TypedDict, cast, final, override

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.data_entry_flow import section
from homeassistant.helpers.selector import (
    FileSelector,  # pyright: ignore[reportUnknownVariableType]
    FileSelectorConfig,
    NumberSelector,  # pyright: ignore[reportUnknownVariableType]
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,  # pyright: ignore[reportUnknownVariableType]
    SelectSelectorConfig,
)

from findmy import (
    AccountStateMapping,
    AsyncAppleAccount,
    AsyncSmsSecondFactor,
    AsyncTrustedDeviceSecondFactor,
    FindMyAccessory,
    FindMyAccessoryMapping,
    InvalidCredentialsError,
    KeyPair,
    KeyPairMapping,
    LocalAnisetteProvider,
    LoginState,
    RemoteAnisetteProvider,
    SmsSecondFactorMethod,
    TrustedDeviceSecondFactorMethod,
    UnhandledProtocolError,
)

from homeassistant.core import callback

from ._entity import (
    SMOOTH_MAX_AGE_HOURS_DEFAULT,
    SMOOTH_RADIUS_M_DEFAULT,
    SMOOTH_WINDOW_DEFAULT,
)
from .const import DOMAIN
from .openhaystack import OpenHaystackAccessory, OpenHaystackAccessoryMapping

if TYPE_CHECKING:
    from typing import Any

    from homeassistant.core import HomeAssistant

type AsyncSecondFactorMethod = AsyncSmsSecondFactor | AsyncTrustedDeviceSecondFactor

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA_ACC_LOGIN = vol.Schema(
    {
        vol.Required("email"): str,
        vol.Required("password"): str,
        "advanced_options": section(
            vol.Schema(
                {
                    vol.Optional("anisette_url"): str,
                },
            ),
            {"collapsed": True},
        ),
    },
)
DATA_SCHEME_ACC_2FA = vol.Schema(
    {
        vol.Required("code"): str,
    },
)

DATA_SCHEME_DEV_CHOOSE = vol.Schema(
    {
        "device_type": SelectSelector(
            SelectSelectorConfig(
                options=["static", "rolling", "openhaystack"],
                translation_key="device_type",
            ),
        ),
    },
)

DATA_SCHEME_DEV_STATIC = vol.Schema(
    {
        vol.Required("name"): str,
        vol.Required("private_key"): str,
    },
)

DATA_SCHEME_DEV_ROLLING = vol.Schema(
    {
        vol.Optional("name"): str,
        vol.Required("file"): FileSelector(FileSelectorConfig(accept=".json,.plist")),
    },
)

DATA_SCHEME_DEV_OPENHAYSTACK = vol.Schema(
    {
        vol.Optional("name"): str,
        vol.Required("file"): FileSelector(FileSelectorConfig(accept=".json")),
    },
)

DATA_SCHEME_ACC_IMPORT = vol.Schema(
    {
        vol.Required("file"): FileSelector(FileSelectorConfig(accept=".json")),
    },
)


class LoginFlowAdvancedOptions(TypedDict):
    anisette_url: str | None


class LoginFlowInput(TypedDict):
    """Input to the login step."""

    email: str
    password: str
    advanced_options: LoginFlowAdvancedOptions


class MfaSubmitInput(TypedDict):
    code: str


class DeviceTypeInput(TypedDict):
    device_type: Literal["static", "rolling", "openhaystack"]


class StaticDeviceInput(TypedDict):
    name: str
    private_key: str


class RollingDeviceInput(TypedDict):
    name: str
    file: str


class OpenHaystackDeviceInput(TypedDict):
    name: str
    file: str


class AccountImportInput(TypedDict):
    file: str


class EntryDataAccount(TypedDict):
    type: Literal["account"]
    account_data: AccountStateMapping


class EntryDataStaticDevice(TypedDict):
    type: Literal["device_static"]
    data: KeyPairMapping


class EntryDataRollingDevice(TypedDict):
    type: Literal["device_rolling"]
    data: FindMyAccessoryMapping


class EntryDataOpenHaystackDevice(TypedDict):
    type: Literal["device_openhaystack"]
    data: OpenHaystackAccessoryMapping


type DeviceEntryData = (
    EntryDataStaticDevice | EntryDataRollingDevice | EntryDataOpenHaystackDevice
)
type EntryData = EntryDataAccount | DeviceEntryData


@final
class InitialSetupConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for initial setup."""

    VERSION, MINOR_VERSION = 1, 1

    def __init__(self, *args, **kwargs) -> None:  # pyright: ignore[reportMissingParameterType]
        """Initialize."""
        super().__init__(*args, **kwargs)

        self._account: AsyncAppleAccount | None = None
        self._2fa_method: AsyncSecondFactorMethod | None = None

        self._error: Exception | None = None
        self._2fa_methods: list[AsyncSecondFactorMethod] = []

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return DeviceOptionsFlow(config_entry)

    @override
    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        _LOGGER.debug("%s Step: user - %s", self.__class__.__name__, user_input)

        return self.async_show_menu(
            step_id="user",
            menu_options={
                "start_acc": "Apple Account (login)",
                "start_acc_import": "Apple Account (import JSON)",
                "start_dev": "FindMy Device",
            },
        )

    ##########################
    ### Account Setup Flow ###
    ##########################

    async def async_step_start_acc(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        _LOGGER.debug("%s Step: start_acc - %s", self.__class__.__name__, user_input)

        return self.async_show_form(
            step_id="acc_login",
            data_schema=DATA_SCHEMA_ACC_LOGIN,
        )

    async def async_step_acc_login(
        self,
        info: LoginFlowInput | None,
    ) -> config_entries.ConfigFlowResult:
        _LOGGER.debug(
            "%s Step: acc_login - %s",
            self.__class__.__name__,
            {**(info or {}), "password": "**REDACTED**"},
        )

        if info is None:
            _LOGGER.warning("Login step called without info data")
            return self.async_show_form(
                step_id="acc_login",
                data_schema=DATA_SCHEMA_ACC_LOGIN,
            )

        _ = await self.async_set_unique_id(info["email"].lower())
        self._abort_if_unique_id_configured()

        _LOGGER.info("Logging into Apple account: %s", info["email"])

        anisette_url = info.get("advanced_options", {}).get("anisette_url") or None
        _LOGGER.info("Using Anisette URL: %s", anisette_url or "<integrated>")

        if anisette_url is None:
            anisette = LocalAnisetteProvider()
        else:
            anisette = RemoteAnisetteProvider(anisette_url)
        self._account = AsyncAppleAccount(anisette=anisette)

        # Attempt login
        try:
            await self._account.login(info["email"], info["password"])
        except InvalidCredentialsError:
            return self.async_show_form(
                step_id="acc_login",
                data_schema=DATA_SCHEMA_ACC_LOGIN,
                errors={"base": "invalid_auth"},
            )
        except UnhandledProtocolError:
            _LOGGER.exception("Unhandled protocol exception during login")
            return self.async_abort(reason="protocol_error")

        _LOGGER.debug("State after login: %s", self._account.login_state)

        if self._account.login_state == LoginState.REQUIRE_2FA:
            return await self.async_step_acc_2fa_prompt()
        return await self.async_step_acc_done()

    async def async_step_acc_2fa_prompt(
        self,
        info: dict[Any, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        _LOGGER.debug("%s Step: acc_2fa_prompt - %s", self.__class__.__name__, info)

        if self._account is None or self._account.login_state != LoginState.REQUIRE_2FA:
            _LOGGER.error("2FA step called but account configured incorrectly")
            return self.async_abort(reason="unknown_error")

        self._2fa_methods = cast(
            "list[AsyncSecondFactorMethod]",
            await self._account.get_2fa_methods(),
        )

        menu_options: list[SelectOptionDict] = []
        for i, method in enumerate(self._2fa_methods):
            if isinstance(method, TrustedDeviceSecondFactorMethod):
                menu_options.append(
                    {
                        "label": "Trusted Device",
                        "value": str(i),
                    },
                )
            elif isinstance(method, SmsSecondFactorMethod):  # pyright: ignore[reportUnnecessaryIsInstance]
                menu_options.append(
                    {
                        "label": f"SMS - {method.phone_number}",
                        "value": str(i),
                    },
                )
            else:
                _LOGGER.warning("Unknown 2FA method: %s", method)
                continue

        if not menu_options:
            return self.async_show_form(
                step_id="acc_login",
                data_schema=DATA_SCHEMA_ACC_LOGIN,
                errors={"base": "2fa_unavailable"},
            )

        schema = vol.Schema(
            {
                "method_id": SelectSelector(
                    SelectSelectorConfig(
                        options=menu_options,
                    ),
                ),
            },
        )
        return self.async_show_form(
            step_id="acc_2fa_request",
            data_schema=schema,
        )

    async def async_step_acc_2fa_request(
        self,
        info: dict[Any, Any] | None,
    ) -> config_entries.ConfigFlowResult:
        _LOGGER.debug("%s Step: acc_2fa_request - %s", self.__class__.__name__, info)

        if info is None:
            _LOGGER.error("2FA requested but no method ID")
            return self.async_abort(reason="unknown_error")

        try:
            method_id: int = int(cast("int", info["method_id"]))
            self._2fa_method = self._2fa_methods[method_id]
        except (KeyError, ValueError):
            _LOGGER.exception("Unable to look up method ID")
            return self.async_abort(reason="unknown_error")

        await self._2fa_method.request()

        return self.async_show_form(
            step_id="acc_2fa_submit",
            data_schema=DATA_SCHEME_ACC_2FA,
        )

    async def async_step_acc_2fa_submit(
        self,
        info: MfaSubmitInput,
    ) -> config_entries.ConfigFlowResult:
        _LOGGER.debug("%s Step: acc_2fa_submit - %s", self.__class__.__name__, info)

        code = info.get("code")
        if not code:
            _LOGGER.error("No 2FA code submitted")
            return self.async_show_form(
                step_id="2fa_submit",
                data_schema=DATA_SCHEME_ACC_2FA,
            )

        if self._2fa_method is None:
            _LOGGER.error("No active 2FA method in the flow")
            return self.async_abort(reason="unknown_error")

        try:
            _ = await self._2fa_method.submit(code)
        except UnhandledProtocolError:
            _LOGGER.exception("Unhandled protocol exception during 2FA submit")
            return self.async_show_form(
                step_id="acc_2fa_submit",
                data_schema=DATA_SCHEME_ACC_2FA,
                errors={"base": "2fa_invalid"},
            )

        return await self.async_step_acc_done()

    ############################
    ### Account Import Flow  ###
    ############################

    async def async_step_start_acc_import(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        _LOGGER.debug("%s Step: start_acc_import - %s", self.__class__.__name__, user_input)
        return self.async_show_form(
            step_id="acc_import",
            data_schema=DATA_SCHEME_ACC_IMPORT,
        )

    async def async_step_acc_import(
        self,
        info: AccountImportInput | None = None,
    ) -> config_entries.ConfigFlowResult:
        _LOGGER.debug("%s Step: acc_import - %s", self.__class__.__name__, info)

        if not info:
            return self.async_show_form(
                step_id="acc_import",
                data_schema=DATA_SCHEME_ACC_IMPORT,
                errors={"base": "invalid_dev"},
            )

        file_id = info.get("file", "")
        account = await self.hass.async_add_executor_job(
            _get_account_from_file, self.hass, file_id,
        )
        if account is None:
            return self.async_show_form(
                step_id="acc_import",
                data_schema=DATA_SCHEME_ACC_IMPORT,
                errors={"base": "invalid_acc_json"},
            )

        # Deduplicate by account email so re-importing the same session doesn't
        # produce two config entries.
        email = account.account_name  # pyright: ignore[reportUnknownMemberType]
        if email:
            _ = await self.async_set_unique_id(email.lower())
            self._abort_if_unique_id_configured()

        self._account = account
        return await self.async_step_acc_done()

    async def async_step_acc_done(
        self,
        info: dict[Any, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        _LOGGER.debug("%s Step: acc_done - %s", self.__class__.__name__, info)

        if self._account is None:
            _LOGGER.exception("No account configured")
            return self.async_abort(reason="unknown_error")

        data = EntryDataAccount(
            type="account",
            account_data=self._account.to_json(),
        )

        return self.async_create_entry(
            title=f"Account: {self._account.account_name}",
            data=data,
        )

    #########################
    ### Device Setup Flow ###
    #########################

    async def async_step_start_dev(
        self,
        info: dict[Any, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        _LOGGER.debug("%s Step: start_dev - %s", self.__class__.__name__, info)

        return self.async_show_form(
            step_id="dev_choose",
            data_schema=DATA_SCHEME_DEV_CHOOSE,
        )

    async def async_step_dev_choose(
        self,
        info: DeviceTypeInput | None = None,
    ) -> config_entries.ConfigFlowResult:
        _LOGGER.debug("%s Step: dev_choose - %s", self.__class__.__name__, info)

        dev_type = (info or {}).get("device_type", None)
        if dev_type == "static":
            return self.async_show_form(
                step_id="dev_static",
                data_schema=DATA_SCHEME_DEV_STATIC,
            )
        if dev_type == "rolling":
            return self.async_show_form(
                step_id="dev_rolling",
                data_schema=DATA_SCHEME_DEV_ROLLING,
            )
        if dev_type == "openhaystack":
            return self.async_show_form(
                step_id="dev_openhaystack",
                data_schema=DATA_SCHEME_DEV_OPENHAYSTACK,
            )

        return self.async_show_form(
            step_id="dev_choose",
            data_schema=DATA_SCHEME_DEV_CHOOSE,
            errors={"base": "invalid_dev"},
        )

    async def async_step_dev_static(
        self,
        info: StaticDeviceInput | None = None,
    ) -> config_entries.ConfigFlowResult:
        _LOGGER.debug("%s Step: dev_static - %s", self.__class__.__name__, info)

        if not info:
            return self.async_show_form(
                step_id="dev_static",
                data_schema=DATA_SCHEME_DEV_STATIC,
                errors={"base": "invalid_dev"},
            )

        name = info.get("name", "Unknown")
        key = info.get("private_key", "")

        try:
            device = KeyPair.from_b64(key)
        except ValueError:
            return self.async_show_form(
                step_id="dev_static",
                data_schema=DATA_SCHEME_DEV_STATIC,
                errors={"base": "invalid_dev_key"},
            )
        device.name = name or "Unknown"

        data = EntryDataStaticDevice(
            type="device_static",
            data=device.to_json(),
        )

        return self.async_create_entry(
            title=f"Device (Static): {name}",
            data=data,
        )

    async def async_step_dev_rolling(
        self,
        info: RollingDeviceInput | None = None,
    ) -> config_entries.ConfigFlowResult:
        _LOGGER.debug("%s Step: dev_rolling - %s", self.__class__.__name__, info)

        if not info:
            return self.async_show_form(
                step_id="dev_rolling",
                data_schema=DATA_SCHEME_DEV_ROLLING,
                errors={"base": "invalid_dev"},
            )

        name = info.get("name", None)
        file_id = info.get("file", "")

        device = await self.hass.async_add_executor_job(_get_device_from_file, self.hass, file_id)
        if device is None:
            return self.async_show_form(
                step_id="dev_rolling",
                data_schema=DATA_SCHEME_DEV_ROLLING,
                errors={"base": "invalid_dev_key"},
            )
        device.name = name or device.name or "Unknown"

        data = EntryDataRollingDevice(
            type="device_rolling",
            data=device.to_json(),
        )

        return self.async_create_entry(
            title=f"Device (Rolling): {device.name}",
            data=data,
        )

    async def async_step_dev_openhaystack(
        self,
        info: OpenHaystackDeviceInput | None = None,
    ) -> config_entries.ConfigFlowResult:
        _LOGGER.debug("%s Step: dev_openhaystack - %s", self.__class__.__name__, info)

        if not info:
            return self.async_show_form(
                step_id="dev_openhaystack",
                data_schema=DATA_SCHEME_DEV_OPENHAYSTACK,
                errors={"base": "invalid_dev"},
            )

        name_override = info.get("name", None)
        file_id = info.get("file", "")

        devices = await self.hass.async_add_executor_job(
            _get_openhaystack_from_file, self.hass, file_id,
        )
        if not devices:
            return self.async_show_form(
                step_id="dev_openhaystack",
                data_schema=DATA_SCHEME_DEV_OPENHAYSTACK,
                errors={"base": "invalid_dev_key"},
            )

        _LOGGER.info(
            "Importing %d OpenHaystack device(s) from devices.json",
            len(devices),
        )

        # For each device after the first: schedule a headless background flow
        # that lands in async_step_openhaystack_bulk and creates its own entry.
        for extra in devices[1:]:
            self.hass.async_create_task(
                self.hass.config_entries.flow.async_init(
                    DOMAIN,
                    context={"source": "openhaystack_bulk"},
                    data=extra.to_json(),
                ),
            )

        # Handle the first device in the current interactive flow.
        device = devices[0]
        if name_override:
            device.name = name_override

        data = EntryDataOpenHaystackDevice(
            type="device_openhaystack",
            data=device.to_json(),
        )

        return self.async_create_entry(
            title=f"Device (OpenHaystack, {len(device.keypairs)} keys): {device.name}",
            data=data,
        )

    async def async_step_openhaystack_bulk(
        self,
        info: OpenHaystackAccessoryMapping | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Headless import path used when the primary openhaystack flow's
        devices.json contained more than one entry.  Called with the mapping
        of a single OpenHaystackAccessory as the flow's initial data.
        """
        _LOGGER.debug(
            "%s Step: openhaystack_bulk - %s",
            self.__class__.__name__,
            info,
        )
        if not info:
            return self.async_abort(reason="unknown_error")

        try:
            device = OpenHaystackAccessory.from_json(info)
        except (ValueError, KeyError, TypeError):
            _LOGGER.exception("Bulk import failed to parse device data")
            return self.async_abort(reason="unknown_error")

        data = EntryDataOpenHaystackDevice(
            type="device_openhaystack",
            data=device.to_json(),
        )

        return self.async_create_entry(
            title=f"Device (OpenHaystack, {len(device.keypairs)} keys): {device.name}",
            data=data,
        )


def _get_device_from_file(hass: HomeAssistant, file_id: str) -> FindMyAccessory | None:
    """Load a FindMyAccessory from an uploaded file."""
    with process_uploaded_file(hass, file_id) as f:
        device = None

        # try plist
        with contextlib.suppress(ValueError):
            device = FindMyAccessory.from_plist(f)

        # try json
        if device is None:
            with contextlib.suppress(ValueError):
                device = FindMyAccessory.from_json(f)

    return device


def _get_openhaystack_from_file(
    hass: HomeAssistant,
    file_id: str,
) -> list[OpenHaystackAccessory] | None:
    """Load a list of OpenHaystackAccessory from an OpenHaystack devices.json file."""
    with process_uploaded_file(hass, file_id) as f:
        try:
            return OpenHaystackAccessory.from_openhaystack_file(str(f))
        except ValueError:
            _LOGGER.exception("Failed to parse OpenHaystack devices.json")
            return None


def _get_account_from_file(
    hass: HomeAssistant,
    file_id: str,
) -> AsyncAppleAccount | None:
    """Load a pre-authenticated AsyncAppleAccount from an exported state JSON.

    The file is expected to be the `to_json()` output of an already-configured
    findmy.py account (or a compatible export from Macless-Haystack). No login
    or 2FA is performed; the tokens inside must still be valid.
    """
    import json  # local import to keep hass startup light

    with process_uploaded_file(hass, file_id) as f:
        try:
            with open(f, encoding="utf-8") as fp:
                data = json.load(fp)
        except (OSError, ValueError):
            _LOGGER.exception("Failed to read account JSON")
            return None

        # Optional wrappers to unwrap.  findmy.py state has multiple top-level
        # keys (account, anisette, ids, login, type) so we must only unwrap
        # when the "outer" dict is clearly a wrapper with a single key.
        if isinstance(data, dict) and set(data.keys()) == {"account_data"}:
            data = data["account_data"]
        elif (
            isinstance(data, dict)
            and set(data.keys()) == {"account"}
            and isinstance(data["account"], dict)
            and "type" not in data["account"]
        ):
            # `{"account": {...actual state...}}` wrapper - only if the inner
            # dict doesn't itself look like a full findmy.py state (which
            # would have "type": "account" at its root).
            data = data["account"]

        try:
            # No anisette provider is attached during import; the account uses
            # the default (integrated) provider unless the user reconfigures.
            return AsyncAppleAccount.from_json(data)
        except (ValueError, KeyError, TypeError):
            _LOGGER.exception("Account JSON is not a valid findmy.py state export")
            return None


# --- Options flow (per-device tunable smoothing) ---------------------------

# Tunable ranges - keep them permissive but not absurd
_OPT_WINDOW_MIN, _OPT_WINDOW_MAX = 3, 100
_OPT_RADIUS_MIN, _OPT_RADIUS_MAX = 20, 5000
_OPT_MAX_AGE_MIN, _OPT_MAX_AGE_MAX = 0.5, 72.0


@final
class DeviceOptionsFlow(config_entries.OptionsFlow):
    """Per-device sliders for the smoothed-position sensors."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry: config_entries.ConfigEntry = config_entry

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        entry_type = self._entry.data.get("type", "")
        if not str(entry_type).startswith("device_"):
            return self.async_abort(reason="no_options")

        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self._entry.options or {}

        schema = vol.Schema(
            {
                vol.Required(
                    "smoothing_window",
                    default=current.get("smoothing_window", SMOOTH_WINDOW_DEFAULT),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=_OPT_WINDOW_MIN,
                        max=_OPT_WINDOW_MAX,
                        step=1,
                        mode=NumberSelectorMode.SLIDER,
                        unit_of_measurement="reports",
                    ),
                ),
                vol.Required(
                    "smoothing_radius_m",
                    default=current.get("smoothing_radius_m", SMOOTH_RADIUS_M_DEFAULT),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=_OPT_RADIUS_MIN,
                        max=_OPT_RADIUS_MAX,
                        step=10,
                        mode=NumberSelectorMode.SLIDER,
                        unit_of_measurement="m",
                    ),
                ),
                vol.Required(
                    "smoothing_max_age_hours",
                    default=current.get("smoothing_max_age_hours", SMOOTH_MAX_AGE_HOURS_DEFAULT),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=_OPT_MAX_AGE_MIN,
                        max=_OPT_MAX_AGE_MAX,
                        step=0.5,
                        mode=NumberSelectorMode.SLIDER,
                        unit_of_measurement="h",
                    ),
                ),
            },
        )

        return self.async_show_form(step_id="init", data_schema=schema)
