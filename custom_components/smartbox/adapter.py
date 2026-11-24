"""HA adapter: entity base, change fan-out + typed state accessors.

Entities read the versioned store (:mod:`smartbox.core.engine`) rather than
holding state: state is fetched per-change from the engine, updates arrive
through ONE shared engine subscription (the fan-out below), and wire-shaped
values are decoded with the :mod:`smartbox.protocol.values` primitives.

The engine takes any number of async subscribers and has no unsubscribe, so
``EngineChangeFanOut`` holds ONE subscription for the runtime's lifetime and
routes changes to per-entity sync callbacks, which it can unregister. Entities
register on ``async_added_to_hass`` and unregister on
``async_will_remove_from_hass``; the runtime builder creates the fan-out once
per entry (``SmartboxRuntime.fanout``).

Callbacks are sync and run inline on the event loop (the engine awaits its
handlers inside ``_notify``), so a callback calls ``async_write_ha_state()``
directly.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Mapping
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from smartbox.core.state import (
    Change,
    DeviceRemoved,
    FeedChange,
    NodeAdded,
    NodeChange,
    NodeRemoved,
    NodeState,
    node_key,
)
from smartbox.protocol.client import NodeRef
from smartbox.protocol.errors import SmartboxError
from smartbox.protocol.values import (
    celsius_to_node_units,
    node_units_to_celsius,
    parse_number,
    parse_number_or_none,
)

from .const import (
    CONF_DISPLAY_ENTITY_PICTURES,
    DEFAULT_BOOST_TEMP,
    DEFAULT_BOOST_TIME,
    DOMAIN,
)
from .devices import node_link
from .exceptions import user_error
from .product import product_label

if TYPE_CHECKING:
    from smartbox.core.engine import SmartboxEngine
    from smartbox.core.state import DeviceState
    from smartbox.protocol.reseller import SmartboxReseller

    from . import SmartboxConfigEntry, SmartboxRuntime
    from .identity import SmartboxDeviceInfo, SmartboxNodeInfo

__all__ = [
    "EngineChangeFanOut",
    "SmartboxAdapterEntity",
    "SmartboxBoxAdapterEntity",
    "SmartboxNodeAdapterEntity",
    "node_boost_params",
    "node_extra_options",
    "node_max_stemp_limit",
    "node_power_limit",
    "node_runback_limits",
    "node_runback_params",
    "node_setup_bool",
    "node_setup_float",
    "node_units",
]


ChangeCallback = Callable[[Change], None]
Unregister = Callable[[], None]

# extra_options.version >= 2 stores temperature fields in Celsius rather than
# the node's display unit (the vendor app's hasExtraOptionTempsInCelsius).
_EXTRA_OPTIONS_CELSIUS_VERSION = 2.0

_LOGGER = logging.getLogger(__name__)


class EngineChangeFanOut:
    """ONE engine subscriber; routes changes to filtered entity callbacks.

    Registered per entity as ``(dev_id, node_key, callback)``: a ``None``
    node_key matches device-level changes, a node_key matches that node's
    ``NodeChange``s. A node registration ALSO receives its device's changes,
    because device slots are rendered inside node entities (climate's
    ``PRESET_AWAY`` comes from the device away flag). Callbacks are sync and
    receive the raw ``Change``.

    The subscription is never cancelled (the engine has no unsubscribe), so
    with zero registrations the fan-out is a no-op.
    """

    def __init__(self, engine: SmartboxEngine) -> None:
        """Subscribe to the engine's change stream."""
        self._engine = engine
        self._regs: list[tuple[str, str | None, ChangeCallback]] = []
        engine.subscribe(self._on_change)

    def register_node(
        self, dev_id: str, node_key: str, callback: ChangeCallback
    ) -> Unregister:
        """Register a node-level callback; return its unregister callable."""
        return self._register(dev_id, node_key, callback)

    def register_device(self, dev_id: str, callback: ChangeCallback) -> Unregister:
        """Register a device-level callback; return its unregister callable."""
        return self._register(dev_id, None, callback)

    def _register(
        self, dev_id: str, node_key: str | None, callback: ChangeCallback
    ) -> Unregister:
        reg = (dev_id, node_key, callback)
        self._regs.append(reg)

        def _unregister() -> None:
            self._regs.remove(reg)

        return _unregister

    async def _on_change(self, change: Change) -> None:
        if isinstance(change, (NodeAdded, NodeRemoved, DeviceRemoved)):
            # Membership changes are not entity state: the membership watcher
            # owns additions and removals, and a removed node's slot changes
            # never arrive. The node's own availability change is separate.
            return
        if isinstance(change, FeedChange):
            # Feed state is global: every entity re-renders, because every
            # value in the store is stale while the feed is down. Every
            # branch iterates a COPY: a callback may register or unregister
            # (an entity added or removed from a state-changed listener), and
            # a live list would silently skip registrations.
            for _reg_dev, _reg_key, callback in list(self._regs):
                self._call(callback, change)
            return
        if isinstance(change, NodeChange):
            for reg_dev, reg_key, callback in list(self._regs):
                if reg_dev == change.dev_id and reg_key == change.node_key:
                    self._call(callback, change)
            return
        # DeviceChange: the device's own entities AND every node entity of
        # that device (device slots render inside node entities too).
        for reg_dev, _reg_key, callback in list(self._regs):
            if reg_dev == change.dev_id:
                self._call(callback, change)

    @staticmethod
    def _call(callback: ChangeCallback, change: Change) -> None:
        """Invoke one entity callback, isolating its failures."""
        try:
            callback(change)
        except Exception:  # one bad entity must not starve the rest
            # Per-registration isolation mirrors the engine's per-subscriber
            # isolation: a failing entity callback must not keep the other
            # entities of the change from rendering.
            _LOGGER.exception("Entity change callback raised; continuing")


class SmartboxAdapterEntity(Entity):
    """Base for entities backed by the core engine.

    Node-level entities take ``(runtime, fanout, dev_id, node_key)``;
    device-level ("box") entities take ``(runtime, fanout, dev_id)``. Both read
    state on demand from the engine's copy-on-write store.

    Availability is the feed's liveness first: a down feed makes every stored
    value stale, so nothing renders available while it lasts. Then it is the
    node's own ``available`` slot for node entities - the ``None`` "unknown"
    value renders UNAVAILABLE, so an entity never guesses availability before
    the wire confirms it - or the device's presence in the store for box
    entities. The box connectivity sensor overrides this and reports
    ``DeviceState.connected`` as its STATE.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self,
        runtime: SmartboxRuntime,
        fanout: EngineChangeFanOut,
        dev_id: str,
        node_key: str | None = None,
    ) -> None:
        """Store the runtime and entity addressing.

        Subclasses own registry identity; this base only addresses the store.
        """
        self._runtime = runtime
        self._fanout = fanout
        self._dev_id = dev_id
        self._node_key = node_key
        self._unregister: Unregister | None = None

    def _apply_display_entity_picture(
        self, entry: SmartboxConfigEntry, reseller: SmartboxReseller
    ) -> None:
        """Honor the reseller-entity-picture option (shared by both bases)."""
        if entry.options.get(CONF_DISPLAY_ENTITY_PICTURES, False) is True:
            self._attr_entity_picture = f"{reseller.web_url}img/favicon.ico"

    def _node_state(self) -> NodeState | None:
        """Return the current node state, or None while it is unknown."""
        if self._node_key is None:
            return None
        return self._runtime.engine.node(self._dev_id, self._node_key)

    def _device_state(self) -> DeviceState | None:
        """Return the current device state, or None while it is unknown."""
        return self._runtime.engine.device(self._dev_id)

    @property
    def available(self) -> bool:
        """None (unknown) renders unavailable; see class docstring.

        A down feed short-circuits to unavailable whatever the store holds -
        every slot in it is stale while the wire is silent. The engine's grace
        window keeps the vendor's scheduled token-expiry reconnect out of this
        signal.

        A box entity's availability is the FEED's alone: ``connected: false``
        is the backend's liveness session for the box, not a statement that its
        data is stale (the box keeps reporting through that window:
        docs/api-contract.md §6.4), so gating on it locked the box controls for
        as long as the session stayed down. The connectivity sensor reports the
        flag as its STATE and overrides this.
        """
        if not self._runtime.engine.feed.available:
            return False
        if self._node_key is None:
            return self._device_state() is not None
        node = self._node_state()
        return node is not None and node.available is True

    async def async_request_call[T](self, coro: Coroutine[Any, Any, T]) -> T:
        """Run one Home Assistant action, translating API failures.

        Home Assistant shows the message of a ``HomeAssistantError`` to the
        user; a protocol error would surface as an unexpected exception with a
        traceback. The custom domain services call entity methods directly and
        keep their own, more specific translations.
        """
        try:
            return await super().async_request_call(coro)
        except SmartboxError as ex:
            msg = "action_failed"
            raise user_error(
                msg,
                entity_id=self.entity_id or "",
                error=str(ex),
            ) from ex

    async def async_added_to_hass(self) -> None:
        """Subscribe to the shared fan-out with this entity's filter."""
        await super().async_added_to_hass()
        if self._node_key is None:
            self._unregister = self._fanout.register_device(
                self._dev_id, self._async_change
            )
        else:
            self._unregister = self._fanout.register_node(
                self._dev_id, self._node_key, self._async_change
            )

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from the fan-out and drop the service target."""
        self._runtime.services.discard(self)
        if self._unregister is not None:
            self._unregister()
            self._unregister = None

    def _async_change(self, _change: Change) -> None:
        """Fan-out callback (sync, on the loop): refresh from the store."""
        # Capability attributes (number min/max/step, climate min/max) are a
        # cached_property in HA and depend on live store state: the node's
        # unit and max_stemp_limit arrive after the entity is added, so the
        # first cache would freeze the Celsius defaults forever.
        self.__dict__.pop("capability_attributes", None)
        self.async_write_ha_state()


class SmartboxNodeAdapterEntity(SmartboxAdapterEntity):
    """Node-level adapter entity: shared identity + device info + status.

    Owns what every node platform would otherwise repeat: registry identity
    (``unique_id`` = ``{node.node_id}_{attr_key}``, translation_key =
    ``attr_key``), the reseller display fields, the deep link, the
    entity-picture option, the node device_info and the ``_status`` view of the
    engine store. ``attr_key`` is the per-entity identity fragment
    (``"thermostat"``, ``"power"``, ``"lock"``, ...).
    """

    _node: SmartboxNodeInfo
    _ref: NodeRef

    def __init__(
        self,
        node: SmartboxNodeInfo,
        entry: SmartboxConfigEntry,
        runtime: SmartboxRuntime,
        fanout: EngineChangeFanOut,
        attr_key: str,
    ) -> None:
        """Store the node identity; address the engine store."""
        super().__init__(
            runtime=runtime,
            fanout=fanout,
            dev_id=node.dev_id,
            node_key=node_key(node.node_type, node.addr),
        )
        self._node = node
        self._ref = NodeRef(node.node_type, str(node.addr))
        self._entry_id = entry.entry_id
        self._attr_unique_id = f"{node.node_id}_{attr_key}"
        self._attr_translation_key = attr_key
        self._reseller = node.reseller
        self._configuration_url = (
            f"{node.reseller.web_url}#/{node.home_id}"
            f"/dev/{node.node_id}/{node.node_type}/{node.addr}/setup"
        )
        self._apply_display_entity_picture(entry, node.reseller)
        _LOGGER.debug("Created node unique_id=%r", self.unique_id)

    @property
    def _status(self) -> dict[str, Any]:
        """The node's current wire status ({} until the first frame)."""
        state = self._node_state()
        return state.status if state is not None else {}

    @property
    def _live_pid(self) -> str | None:
        """The PID from the live version slot, falling back to discovery.

        A node whose REST version read failed still gets its PID from the
        first pushed version frame; runtime semantics must use that value.
        """
        state = self._node_state()
        version = state.version if state is not None else None
        if isinstance(version, Mapping):
            pid = version.get("pid")
            if isinstance(pid, str) and pid:
                return pid
        return self._node.pid

    @property
    def _live_fw_version(self) -> str | None:
        """The firmware version from the live version slot, else discovery."""
        state = self._node_state()
        version = state.version if state is not None else None
        if isinstance(version, Mapping):
            fw_version = version.get("fw_version")
            if isinstance(fw_version, str) and fw_version:
                return fw_version
        return self._node.version.get("fw_version")

    @property
    def device_info(self) -> DeviceInfo:
        """Return the node device info, linked to its box device."""
        pid = self._node.pid
        hw_version = self._node.hw_version
        fw_version = self._node.version.get("fw_version")
        info = DeviceInfo(
            identifiers={(DOMAIN, self._node.node_id)},
            name=self._node.name,
            manufacturer=self._reseller.name,
            # The vendor's EProductCodeValue enum gives the control category
            # from pid[2:4] (triac/relay heater, heater module, storage heater,
            # water storage heater, thermostat, valve, WiFi heater, power
            # monitor). The wire address stays in the model text for
            # diagnostics. A towel rail is an add-device/UI category, not a
            # PID code, so it shows its control category rather than being
            # guessed.
            model=(
                f"{product_label(self._node.node_type, pid)} "
                f"({self._node.node_type}, addr {self._node.addr})"
            ),
            model_id=str(pid) if pid else None,
            hw_version=str(hw_version) if hw_version else None,
            sw_version=str(fw_version) if fw_version else None,
            configuration_url=self._configuration_url,
        )
        info.update(node_link(self.hass, self._entry_id, self._node.dev_id))
        return info


class SmartboxBoxAdapterEntity(SmartboxAdapterEntity):
    """Device-level ("box") adapter entity.

    The box (the gateway the nodes talk through) is its own HA device, and
    device-level data (connectivity, away status, power limit) belongs there.
    The ``SmartboxDeviceInfo`` identity from REST discovery is kept only for
    registry identity and device info; state reads go through the engine's
    device store.
    """

    _device: SmartboxDeviceInfo

    def __init__(
        self,
        device: SmartboxDeviceInfo,
        entry: SmartboxConfigEntry,
        runtime: SmartboxRuntime,
        fanout: EngineChangeFanOut,
        attr_key: str,
    ) -> None:
        """Store the device identity; address the engine store."""
        super().__init__(runtime=runtime, fanout=fanout, dev_id=device.dev_id)
        self._device = device
        self._attr_unique_id = f"{device.dev_id}_{attr_key}"
        self._attr_translation_key = attr_key
        self._reseller = device.reseller
        self._configuration_url = device.configuration_url
        self._apply_display_entity_picture(entry, device.reseller)

    @property
    def device_info(self) -> DeviceInfo:
        """Return the box device info (this base's formula)."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._dev_id)},
            name=self._device.name,
            manufacturer=self._reseller.name,
            model_id=str(self._device.model_id),
            sw_version=str(self._device.sw_version),
            serial_number=str(self._device.serial_number),
            configuration_url=self._configuration_url,
        )


def node_units(state: NodeState | None) -> str | None:
    """Return the node's temperature unit ("C"/"F") from ``status["units"]``.

    None when absent or not a string.
    """
    if state is None:
        return None
    units = state.status.get("units")
    return units if isinstance(units, str) else None


def node_setup_bool(state: NodeState | None, key: str) -> bool | None:
    """Return a boolean setup key (``window_mode_enabled``, ``true_radiant_enabled``).

    A JSON bool in the node setup. None when absent, not a bool, or the node is
    unknown: entities render UNKNOWN rather than raising.
    """
    if state is None:
        return None
    value = state.setup.get(key)
    return value if isinstance(value, bool) else None


def node_setup_float(state: NodeState | None, key: str) -> float | None:
    """Return a stringified numeric setup key (``away_offset`` style) as a float.

    The wire is stringly typed for setup numbers ("2.0", "12.5"). None when
    absent or None; any other malformed value raises ``SmartboxError``.
    """
    if state is None:
        return None
    value = state.setup.get(key)
    if value is None:
        return None
    try:
        return parse_number(value)
    except SmartboxError as ex:
        # Server-controlled setup data: an entity property must render
        # unknown, not stop every state write for the entity.
        _LOGGER.debug("Ignoring malformed setup %r: %s", key, ex)
        return None


def node_max_stemp_limit(state: NodeState | None) -> float | None:
    """Return the setup ``max_stemp_limit`` ceiling IN THE NODE'S UNIT.

    The wire field is Celsius regardless of the node's display unit (the
    vendor app converts both ways, docs/api-contract.md §5.2/§7 item 2), so
    the value is converted into the node's unit before any entity sees it.
    A stringified float; ``"0.0"`` is the DISABLED sentinel
    (docs/api-contract.md §5 live fixtures), so any value <= 0 reads as None.
    The memory of the last non-zero limit lives with the limit switch
    (``RestoreEntity``), not here.
    """
    limit = node_setup_float(state, "max_stemp_limit")
    if limit is None or limit <= 0:
        return None
    units = state.status.get("units") if state is not None else None
    if not isinstance(units, str) or units not in ("C", "F"):
        return limit  # no usable unit: leave the value in the wire's scale
    return celsius_to_node_units(limit, units)


def node_max_stemp_limit_c(state: NodeState | None) -> float | None:
    """Return the setup ``max_stemp_limit`` ceiling in Celsius.

    The wire field is Celsius; the entity-facing helper converts it to the
    node's display unit. Programme ranges are computed in Celsius and need
    the raw value. ``0.0`` is the disabled sentinel.
    """
    limit = node_setup_float(state, "max_stemp_limit")
    if limit is None or limit <= 0:
        return None
    return limit


def node_power_limit(state: NodeState | None) -> int | None:
    """Return the node's OWN power limit from ``setup["power_limit"]``.

    A separate setting from the box ``htr_system`` limit, observed on pmo
    nodes; the app writes it through the node's own endpoint
    (docs/api-ws-user.md §2.3). The live capture carries it as a number; an
    absent or unparseable value reads as None (entity properties must not
    raise on malformed wire data).
    """
    if state is None:
        return None
    value = state.setup.get("power_limit")
    if value is None:
        return None
    number = parse_number_or_none(value)
    if number is None:
        return None
    return int(number)


def node_extra_options(state: NodeState | None) -> dict[str, Any]:
    """Return the setup's ``extra_options`` object (empty when absent).

    A JSON object under the node setup; an absent or non-object value reads as
    empty.
    """
    if state is None:
        return {}
    extra = state.setup.get("extra_options")
    return extra if isinstance(extra, dict) else {}


def extra_options_temps_in_celsius(state: NodeState | None) -> bool:
    """Whether ``extra_options`` temperature values are Celsius (version >= 2).

    The vendor app's ``hasExtraOptionTempsInCelsius`` reads
    ``setup.extra_options.version`` and switches the boost/runback/setback
    temperature scale at 2. Older nodes keep those fields in node units.
    """
    if state is None:
        return False
    version = node_extra_options(state).get("version")
    if isinstance(version, bool) or not isinstance(version, (int, float, str)):
        return False
    parsed = parse_number_or_none(version)
    return parsed is not None and parsed >= _EXTRA_OPTIONS_CELSIUS_VERSION


def _extra_temp_to_node_units(state: NodeState | None, value: object) -> float | None:
    """Decode an ``extra_options`` temperature into the node's scale."""
    number = parse_number_or_none(value)
    if number is None:
        return None
    if node_units(state) == "F" and extra_options_temps_in_celsius(state):
        return celsius_to_node_units(number, "F")
    return number


def extra_temp_from_node_units(state: NodeState | None, value: float) -> float:
    """Encode a node-scale temperature for ``extra_options``.

    ``extra_options.version >= 2`` stores temperatures in Celsius even on a
    Fahrenheit node. A version-less/older extra_options block uses the node's
    scale, so the value passes through.
    """
    if node_units(state) == "F" and extra_options_temps_in_celsius(state):
        return node_units_to_celsius(value, "F")
    return value


def node_boost_params(state: NodeState | None) -> tuple[float | None, float | None]:
    """Return ``(boost_temp, boost_time_min)`` decoded from ``extra_options``.

    Absent keys fall back to DEFAULT_BOOST_TEMP / DEFAULT_BOOST_TIME; a
    malformed present value reads as None, because entity properties must not
    raise on malformed wire data. ``extra_options.version >= 2`` stores
    temperatures in Celsius, so those are converted into the node's scale.
    """
    extra = node_extra_options(state)
    units = node_units(state)
    params: list[float | None] = []
    for key, default in (
        ("boost_temp", DEFAULT_BOOST_TEMP),
        ("boost_time", DEFAULT_BOOST_TIME),
    ):
        if key == "boost_temp":
            if key in extra:
                params.append(_extra_temp_to_node_units(state, extra[key]))
            else:
                value = float(default)
                if units == "F":
                    value = celsius_to_node_units(value, "F")
                params.append(value)
            continue
        params.append(parse_number_or_none(extra.get(key, default)))
    return params[0], params[1]


def node_runback_params(state: NodeState | None) -> tuple[float | None, float | None]:
    """Return ``(runback_temp, runback_time_min)`` for a runback activation.

    The app uses ``extra_options.default_runback_temp`` and falls back to the
    current ``status.stemp`` for the temperature; the duration uses
    ``extra_options.boost_time`` (the app's runback default source) or the
    shared default. Values are returned in the node's own scale.
    """
    extra = node_extra_options(state)
    temp_value: object = extra.get("default_runback_temp")
    if temp_value is not None:
        temperature = _extra_temp_to_node_units(state, temp_value)
    elif state is not None:
        temperature = parse_number_or_none(state.status.get("stemp"))
    else:
        temperature = None
    minutes = parse_number_or_none(extra.get("boost_time", DEFAULT_BOOST_TIME))
    return temperature, minutes


def node_runback_limits(
    state: NodeState | None,
) -> tuple[float | None, float | None]:
    """Return the node's runback ``(min_temp, max_temp)`` in node units.

    The app's lower bound is the node's frost temperature (or its setback
    temperature on nodes that expose setback); the upper bound is
    ``max_runback_temp`` from the setup/extra options, clamped by the global
    limit in the entity. Missing values leave the entity's default bound.
    """
    if state is None:
        return None, None
    status = state.status
    extra = node_extra_options(state)
    # The app prefers the setback temperature where that mode exists; the
    # setup field is the practical marker we can observe.
    min_raw = extra.get("setback_temp", state.setup.get("setback_temp"))
    if min_raw is not None:
        min_temp = _extra_temp_to_node_units(state, min_raw)
    else:
        min_temp = parse_number_or_none(status.get("ice_temp"))
    max_raw = extra.get("max_runback_temp")
    if max_raw is not None:
        max_temp = _extra_temp_to_node_units(state, max_raw)
    else:
        max_temp = parse_number_or_none(state.setup.get("max_runback_temp"))
    return min_temp, max_temp
