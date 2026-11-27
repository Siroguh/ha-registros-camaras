# Registros Cámaras para Home Assistant

Integración de **Registros Cámaras 2**, mantenida en
[Siroguh/registros_camaras_home_assistant](https://github.com/Siroguh/registros_camaras_home_assistant).
La versión 1.0 sustituye al cliente del antiguo servidor `registros_camaras`.
Usa la API autenticada de la aplicación nueva; no consulta directamente D1.

## Instalación y configuración

Requiere Home Assistant **2026.10.3 o posterior** y un servidor que implemente
`GET /api/integration/:centreId`, versión de contrato 1.

1. Añade este repositorio como integración personalizada en HACS, o en la
   Marketplace integrada de HA 2026.11 o posterior.
2. Instala y reinicia Home Assistant.
3. En **Ajustes → Dispositivos y servicios → Añadir integración**, busca Registros Cámaras.
4. Introduce la URL raíz de la aplicación, el centro (por defecto `villarroya-01-01`)
   y una **clave API nativa de Better Auth**, creada desde **Cuenta** en la aplicación.

La URL debe ser accesible desde HA. Para desarrollo puede ser
`https://omarchythinkpad.powan-bee.ts.net:1301` mientras `vp run dev` esté activo.
No uses `localhost` para apuntar a otro ordenador. Se valida TLS y no se siguen
redirecciones de las peticiones autenticadas. Las claves antiguas `rc_` no sirven.
La integración nunca entrega la clave a las tarjetas ni a los diagnósticos.

No se ha enviado este repositorio al catálogo público: se instala como repositorio
personalizado. `hacs.json` sirve para HACS y Marketplace; no es una app/add-on de HA OS.

## Entidades

Un dispositivo de centro contiene conexión, tareas pendientes/vencidas y actualizar.
Cada cámara tiene:

- Cantidad y número de lotes (con ocupantes en atributos).
- Estado del plan: vacía, sin plan/fase, compatible o conflicto.
- Mínimo/máximo de temperatura y humedad **efectivos**, no lecturas del sensor.
- Propuesta y sus límites/ID; propuesta pendiente/vencida, conflicto y manual activa.

Los sensores físicos de temperatura/humedad siguen siendo los de HA. Sondeo compartido
cada 30 segundos. Las altas de cámaras se descubren sin reiniciar. Si falla la conexión,
la cámara está archivada o el servidor está reconciliando, las entidades operativas
están `unavailable`, nunca representan cero existencias por un fallo de lectura.
Los atributos `centre_id`, `location_id`, `revision` y `active_lots` permiten construir
tarjetas y conservar los indicadores visuales de cámaras ocupadas.

## Acciones nativas

Las llamadas de usuario requieren administrador de Home Assistant, como en el
bridge anterior. Las automatizaciones internas pueden utilizar las mismas acciones.

Todas están bajo `registros_camaras`, tienen campos en el editor de acciones y
admiten `return_response`/`response_variable`:

| Acción | Uso |
| --- | --- |
| `get_snapshot` | Estado versionado, catálogo, cámaras y tareas pendientes. |
| `get_lots` | Página de inventario; continuar con `nextBefore` como `before`. |
| `get_lot` | Ficha; comprobar `status: projected`. |
| `get_movement_options` | Disponibilidad al día efectivo; `correcting` opcional. |
| `get_movement_history` | Operaciones; comprobar `status: projected`. |
| `confirm_proposal` | Confirmar exactamente `proposal_id` y revisión mostrados. |
| `schedule_manual`, `replace_manual`, `finish_manual` | Ciclo auditado de consignas manuales. |
| `complete_task` | Resultado tipado y `task_revision`; retirada no mueve stock. |
| `create_lot` | Alta con código, cantidad y día local. |
| `create_movement`, `correct_movement` | Repartos, mezcla, procedencias y motivo de corrección. |

Siempre se selecciona `config_entry_id`. Los comandos exigen un UUID `command_id`
y `expected_revision` del centro. Guarda ambos junto con el contenido: reintentar
la misma operación conserva el UUID, revisión y datos, incluso tras reiniciar HA.
Nunca se reintenta automáticamente una escritura con otra revisión. Un conflicto
requiere consultar y revisar los datos; un fallo de transporte puede significar que
ya fue aceptada. Una lectura fallida después de la aceptación no oculta su recibo.

## Tarjetas

Se pueden usar tarjetas nativas Entity/Entities/Tile/History con estas entidades.
Los nombres de entidad los asigna HA; selecciónalos mediante el editor visual.

Las tarjetas personalizadas
[registros_camaras_cards](https://github.com/Siroguh/registros_camaras_cards) **1.0+**
usan estas mismas acciones nativas. Conservan cámara/consignas, alta de lotes,
movimientos totales/parciales, procedencias, corrección, diálogos y editores.
El bridge WebSocket `registros_camaras/operation` anterior se ha retirado.

Para una tarjeta propia:

```js
const { response: snapshot } = await hass.callWS({
  type: "call_service", domain: "registros_camaras", service: "get_snapshot",
  service_data: { config_entry_id: entryId }, return_response: true
});
// Mostrar snapshot; conservar su revisión y el ID que el usuario revisó.
// Crear una vez con crypto.randomUUID(), y conservar para reintentos.
await hass.callWS({
  type: "call_service", domain: "registros_camaras", service: "confirm_proposal",
  service_data: { config_entry_id: entryId, command_id: commandId,
    expected_revision: snapshot.revision, location_id: locationId, proposal_id: proposalId },
  return_response: true
});
```

## Actualizar desde el cliente antiguo

La entrada v1 se migra a v2 y pide la dirección y clave del servidor nuevo.
Actualizar la integración Python requiere reiniciar HA; actualizar solo las tarjetas
requiere recargar el navegador. Sustituir los archivos debe eliminar también los
antiguos `bridge.py` y `notifications.py`. Las reglas de notificación permanecen en
el servidor; este cliente no añade avisos automáticos.

Los identificadores internos de entidad han cambiado. Al sustituir una entrada,
hay que portar las referencias del dashboard (incluidas las embebidas en estilos
Bubble Card), y retirar recursos/entidades antiguos. Conservar previamente copia
del dashboard y del componente. La nueva tarjeta acepta las mismas opciones YAML:
`entry_id`, `location_id`, `title`, `temperature_entity`, `humidity_entity`.

## Plantilla y desarrollo

Estructura basada en la plantilla recomendada por HACS
[custom-components/blueprint](https://github.com/custom-components/blueprint), commit
`cd399dac2a7e88d899dd62b3f5d8bbf91f1adadd`: config flow, cliente asíncrono,
`DataUpdateCoordinator`, `CoordinatorEntity`, `ConfigEntry.runtime_data` y descarga.
Se adaptó a las APIs actuales de HA y al servidor. Licencia/atribución en `NOTICE`.

Referencias consultadas el 27/11/2026:
- https://www.hacs.xyz/docs/publish/integration/
- https://contributors.home-assistant.io/marketplace/guides/integrations/
- https://contributors.home-assistant.io/marketplace/reference/hacs-json/

```sh
uv sync --locked
sh scripts/check.sh
```

Pruebas con Home Assistant real 2026.10.3, HTTP local, flujos, reautenticación,
entidades, descubrimiento, disponibilidad, servicios, descarga y recarga.
El plugin de fixtures se fija por separado y su dependencia HA se ajusta a la
versión mínima con `override-dependencies`. No instala dependencias de tests en HA.
`package.py` genera `dist/registros_camaras.zip` sin credenciales ni cachés.

Desde el servidor, la prueba cruzada opcional ejecuta el cliente Python contra un
Worker/D1/R2 desechable y comprueba Bearer, snapshot y rechazo de revisión obsoleta:

```sh
REGISTROS_HA_CHECKOUT=/ruta/registros_camaras_home_assistant \
  vp test run scripts/observations.test.ts -t 'real Worker persists operational tasks'
```
