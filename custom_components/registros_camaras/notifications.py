"""Application-lifetime, disk-backed relay for administrator mobile responses."""

import asyncio
from datetime import timedelta
import json
import logging
from pathlib import Path
import re
from uuid import uuid4

from homeassistant.components import persistent_notification
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util.file import WriteError, write_utf8_file_atomic

from .api import ApiError, AuthenticationError, RegistrosClient, RequestError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
_ACTION = re.compile(r"^rcn:([a-f0-9]{16}):([a-f0-9-]{36}):(acknowledge|confirm|reject)$")
_RELAYS = f"{DOMAIN}_notification_relays"


class NotificationRelay:
    """One queue owner per entry, retained across setup retries and reloads."""

    def __init__(self, hass, entry):
        self.hass = hass
        self.entry_id = entry.entry_id
        self.path = Path(
            hass.config.path(".storage", f"{DOMAIN}.{entry.entry_id}.notification_relay")
        )
        self.lock = asyncio.Lock()
        self.loaded = False
        self.scope = None
        self.account_id = entry.unique_id
        self.pending = []
        self.task = None
        self.cursor = 0
        self.removed = False

    def _load(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": 1, "account_id": self.account_id, "scope": None, "pending": []}

    async def initialize(self):
        async with self.lock:
            if self.loaded or self.removed:
                return
            saved = await self.hass.async_add_executor_job(self._load)
            if (
                not isinstance(saved, dict)
                or saved.get("version") != 1
                or saved.get("account_id") != self.account_id
                or not isinstance(saved.get("pending"), list)
                or (
                    saved.get("scope") is not None
                    and (
                        not isinstance(saved["scope"], str)
                        or not re.fullmatch(r"[a-f0-9]{16}", saved["scope"])
                    )
                )
            ):
                raise ValueError("Invalid notification relay storage; restore its backup")
            for item in saved["pending"]:
                if not isinstance(item, dict) or not all(
                    isinstance(item.get(key), str)
                    for key in ("id", "scope", "occurrence", "action", "user_id")
                ):
                    raise ValueError("Invalid notification response; restore its backup")
            self.scope = saved.get("scope")
            self.pending = saved["pending"]
            self.loaded = True

    def _write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Unlike Store.async_save, this propagates write/fsync errors to the caller.
        write_utf8_file_atomic(str(self.path), json.dumps(data), private=True)

    async def save(self, pending, scope):
        # Always called under the sole owner's lock. Memory changes only after commit.
        if self.removed:
            return
        await self.hass.async_add_executor_job(
            self._write,
            {"version": 1, "account_id": self.account_id, "scope": scope, "pending": pending},
        )
        self.pending = pending
        self.scope = scope

    def storage_error(self, phase="enqueue"):
        _LOGGER.exception("No se pudo persistir una respuesta de notificación")
        messages = {
            "enqueue": "No se pudo guardar la respuesta. Reintenta o responde desde la aplicación.",
            "load": f"No se pudo leer la cola de respuestas de esta entrada: {self.path.name}. "
            "Se conserva el archivo y se detiene solo este relay. "
            "Restaura su copia y recarga la entrada. "
            "Los controles de cámara siguen disponibles.",
            "maintenance": "No se pudo actualizar la cola de respuestas. "
            "Se conserva el UUID original "
            "para reintento automático; no vuelvas a pulsar la respuesta.",
            "remove": f"No se pudo eliminar la cola local {self.path.name}. "
            "El relay está detenido; elimina el archivo al resolver el error de almacenamiento.",
        }
        persistent_notification.async_create(
            self.hass,
            messages[phase],
            title="Almacenamiento de respuestas",
            notification_id=f"{DOMAIN}_{self.entry_id}_response_storage",
        )

    def entry(self):
        if self.removed:
            return None
        entry = self.hass.config_entries.async_get_entry(self.entry_id)
        return entry if entry and entry.disabled_by is None else None

    async def capture(self, event):
        value = event.data.get("action")
        match = _ACTION.fullmatch(value) if isinstance(value, str) else None
        if not match or not self.entry():
            return
        try:
            await self.initialize()
        except OSError, ValueError, WriteError:
            self.storage_error("load")
            return
        if match[1] != self.scope:
            return
        user_id = event.context.user_id
        user = await self.hass.auth.async_get_user(user_id) if user_id else None
        if not user or not user.is_active or not user.is_admin:
            persistent_notification.async_create(
                self.hass,
                "Solo un administrador de Home Assistant puede responder a estos avisos.",
                title="Respuesta no autorizada",
                notification_id=f"{DOMAIN}_response_unauthorized",
            )
            return
        item = {
            "id": str(uuid4()),
            "scope": match[1],
            "occurrence": match[2],
            "action": match[3],
            "user_id": user_id,
        }
        try:
            async with self.lock:
                await self.save([*self.pending, item], self.scope)
        except OSError, ValueError, WriteError:
            self.storage_error()
            return
        self.retry()

    @callback
    def retry(self, _now=None):
        if self.loaded and self.entry() and (self.task is None or self.task.done()):
            self.task = self.hass.async_create_background_task(
                self.flush(), f"{DOMAIN} notification responses {self.entry_id}"
            )

    async def flush(self):
        entry = self.entry()
        if not entry:
            return
        credentials = dict(entry.data)
        client = RegistrosClient(
            async_get_clientsession(self.hass), credentials[CONF_URL], credentials[CONF_API_KEY]
        )
        try:
            # Every pass authenticates the current endpoint; cached routing identity
            # permits offline intake, never delivery to an unverified replacement.
            status = await client.async_get_status()
            if self.entry() is not entry or entry.data != credentials:
                return
            if status.account_id != self.account_id or not status.notification_scope:
                return
            if "notifications:respond" not in status.capabilities:
                return
            async with self.lock:
                if status.notification_scope != self.scope:
                    await self.save(self.pending, status.notification_scope)
                # Only the first response per occurrence is eligible in each pass.
                heads = {}
                for item in self.pending:
                    if item["scope"] == status.notification_scope:
                        heads.setdefault(item["occurrence"], item)
                items = list(heads.values())
            if not items:
                return
            start = self.cursor % len(items)
            batch = (items[start:] + items[:start])[:50]
            self.cursor = (start + len(batch)) % len(items)
            for item in batch:
                if self.entry() is not entry or entry.data != credentials:
                    return
                user = await self.hass.auth.async_get_user(item["user_id"])
                if not user or not user.is_active or not user.is_admin:
                    persistent_notification.async_create(
                        self.hass,
                        "Respuesta descartada localmente: "
                        "el usuario ya no es un administrador activo. "
                        "No se ha enviado al servidor.",
                        title="Respuesta no autorizada",
                        notification_id=f"{DOMAIN}_response_{item['id']}",
                    )
                    async with self.lock:
                        await self.save(
                            [row for row in self.pending if row["id"] != item["id"]], self.scope
                        )
                    continue
                try:
                    await client.async_notification_action(
                        item["occurrence"], item["id"], item["action"]
                    )
                except AuthenticationError:
                    raise
                except RequestError as err:
                    persistent_notification.async_create(
                        self.hass,
                        str(err),
                        title="Respuesta de notificación rechazada",
                        notification_id=f"{DOMAIN}_response_{item['id']}",
                    )
                except ApiError:
                    # A failing occurrence must not starve unrelated responses.
                    continue
                async with self.lock:
                    await self.save(
                        [row for row in self.pending if row["id"] != item["id"]], self.scope
                    )
        except AuthenticationError:
            if self.entry() is entry and entry.data == credentials:
                entry.async_start_reauth(self.hass)
        except ApiError:
            return
        except OSError, ValueError, WriteError:
            self.storage_error("maintenance")

    async def remove(self):
        """Permanent deletion only: stop scheduling, drain writes, then remove disk state."""
        self.removed = True
        if self.task and not self.task.done():
            await asyncio.gather(self.task, return_exceptions=True)
        async with self.lock:
            try:
                await self.hass.async_add_executor_job(self._remove_file)
            except OSError:
                self.storage_error("remove")

    def _remove_file(self):
        self.path.unlink(missing_ok=True)


async def async_setup_notifications(hass, entry=None):
    """Register intake before coordinator refresh, keeping one writer across reloads."""
    if _RELAYS not in hass.data:
        relays = hass.data[_RELAYS] = {}

        async def action(event):
            for relay in tuple(relays.values()):
                await relay.capture(event)

        @callback
        def retry(_now=None):
            for relay in relays.values():
                relay.retry()

        hass.bus.async_listen("mobile_app_notification_action", action)
        async_track_time_interval(hass, retry, timedelta(seconds=30))
    relays = hass.data[_RELAYS]
    entries = [
        current
        for current in ([entry] if entry else hass.config_entries.async_entries(DOMAIN))
        if current.disabled_by is None
    ]
    for current in entries:
        if current.entry_id not in relays:
            relays[current.entry_id] = NotificationRelay(hass, current)
    for current in entries:
        relay = relays[current.entry_id]
        try:
            await relay.initialize()
        except OSError, ValueError, WriteError:
            relay.storage_error("load")
            continue
        relay.retry()


async def async_remove_notifications(hass, entry):
    relay = hass.data.get(_RELAYS, {}).pop(entry.entry_id, None)
    if relay is None:
        relay = NotificationRelay(hass, entry)
    await relay.remove()
