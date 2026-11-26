"""Persist and relay notification responses; the application owns all rules and sends."""

import asyncio
from datetime import timedelta
import logging
import re
from uuid import uuid4

from homeassistant.components import persistent_notification
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store

from .api import ApiError, AuthenticationError, RequestError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
_ACTION = re.compile(r"^rcn:([a-f0-9]{16}):([a-f0-9-]{36}):(acknowledge|confirm|reject)$")


async def async_setup_notifications(hass, entry):
    storage = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.notification_actions")
    saved = await storage.async_load() or {"pending": []}
    pending = saved.get("pending", [])
    lock = asyncio.Lock()
    running = False

    async def flush():
        nonlocal running
        if running:
            return
        running = True
        try:
            while True:
                async with lock:
                    if not pending:
                        return
                    await storage.async_save({"pending": pending})
                    item = dict(pending[0])
                try:
                    await entry.runtime_data.client.async_notification_action(
                        item["occurrence"], item["id"], item["action"]
                    )
                except AuthenticationError:
                    entry.async_start_reauth(hass)
                    return
                except RequestError as err:
                    persistent_notification.async_create(
                        hass,
                        str(err),
                        title="Respuesta de notificación rechazada",
                        notification_id=f"{DOMAIN}_response_{item['id']}",
                    )
                except ApiError:
                    # Keep the same action UUID on disk until the server accepts
                    # it or definitively rejects it (stale proposal, etc.).
                    return
                async with lock:
                    pending[:] = [row for row in pending if row["id"] != item["id"]]
                    await storage.async_save({"pending": pending})
        finally:
            running = False

    @callback
    def retry(_now=None):
        entry.async_create_background_task(hass, flush(), "registros-notification-responses")

    async def action(event):
        value = event.data.get("action")
        match = _ACTION.fullmatch(value) if isinstance(value, str) else None
        status = entry.runtime_data.data
        if not match or not status or match[1] != status.notification_scope:
            return
        item = {"id": str(uuid4()), "occurrence": match[2], "action": match[3]}
        try:
            async with lock:
                pending.append(item)
                # Save before contacting the server, not a delayed storage write.
                await storage.async_save({"pending": pending})
        except OSError, ValueError:
            _LOGGER.exception("No se pudo persistir una respuesta de notificación")
            persistent_notification.async_create(
                hass,
                "No se pudo guardar la respuesta. Reintenta o responde desde la aplicación.",
                title="Respuesta no guardada",
                notification_id=f"{DOMAIN}_response_storage",
            )
            return
        retry()

    entry.async_on_unload(hass.bus.async_listen("mobile_app_notification_action", action))
    entry.async_on_unload(async_track_time_interval(hass, retry, timedelta(seconds=30)))
    retry()
