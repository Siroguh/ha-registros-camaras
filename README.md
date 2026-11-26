# Registros Cámaras para Home Assistant

Esta integración pertenece al proyecto **[Siroguh/registros_camaras](https://github.com/Siroguh/registros_camaras)**,
su repositorio padre y servidor API. El código fuente, las incidencias y las releases
de la integración se mantienen exclusivamente en este repositorio independiente:
**[Siroguh/registros_camaras_home_assistant](https://github.com/Siroguh/registros_camaras_home_assistant)**.

Cliente de la API REST de **Registros Cámaras**, instalable como integración personalizada
de HACS. Requiere Home Assistant **2026.10 o posterior** y el servidor de Registros Cámaras
con controles operativos y confirmaciones compartidas (migración `0018`).

## Qué incluye esta versión

- Configuración desde **Ajustes → Dispositivos y servicios → Añadir integración**.
- Conexión con la URL de la aplicación y una clave de integración propia.
- Una entidad de diagnóstico **Conexión**, actualizada cada 60 segundos.
- Reintento de conexión, reautenticación si se revoca la clave y reconfiguración de la URL.
- Diagnósticos sin claves, direcciones del servidor ni datos personales.
- Interfaz en español e inglés.

La versión 0.2 incorpora entidades por cámara: rangos de consigna vigente, origen,
fin de consigna manual, indicador de cambio pendiente y botón de confirmación.
Ofrece acciones `create_lot`, `register_movement`, `set_manual` y `confirm_change`.
La versión 0.3 añade `update_movement`, `update_manual` y `finish_manual`, además
de las lecturas `movements` y `manuals` en el puente WebSocket.
Los campos se muestran en la interfaz de acciones de HA y se pueden usar en automatizaciones.
Los controles interactivos requieren un administrador de HA; las automatizaciones locales
también pueden llamar a las acciones. Todas las operaciones se validan en la API.

Las entidades de cámara exponen `active_lots`: número de lotes con existencias actuales
según la API. Los indicadores ambientales del panel pueden limitarse a cámaras ocupadas
y comparar cada lectura con sus límites de consigna vigente, incluidos los manuales.

Para los formularios visuales instala un único complemento adicional de HACS:
[Registros Cámaras — Tarjetas](https://github.com/Siroguh/registros_camaras_cards).
Incluye tres tarjetas independientes y editores visuales, sin copiar archivos ni escribir YAML.

## Instalar con HACS

1. En **HACS → menú de tres puntos → Repositorios personalizados**, añade:
   `https://github.com/Siroguh/registros_camaras_home_assistant`.
2. Selecciona el tipo **Integración**.
3. Busca **Registros Cámaras** en HACS e instálala.
4. Reinicia Home Assistant.
5. En **Ajustes → Dispositivos y servicios → Añadir integración**, busca **Registros Cámaras**.

El repositorio es exclusivamente personalizado. No se solicita ni se pretende su inclusión
en el catálogo general. La validación HACS comprueba formato técnico, no registra el proyecto.
HACS gestiona los archivos del cliente y sus actualizaciones, no el servidor de la aplicación.

## Conectar

1. Abre el cliente web de Registros Cámaras e inicia sesión.
2. En **Home Assistant → Cliente de Home Assistant**, escribe un nombre y pulsa **Crear clave**.
3. Copia la clave `rc_…`, que solo se muestra en ese momento.
4. En el formulario de la integración de HA, introduce:
   - **URL de la aplicación:** su origen con protocolo y puerto, por ejemplo
     `https://app.example.com`, sin `/api` ni ruta de página.
   - **Clave de integración:** la clave que acabas de crear.

La URL debe ser accesible **desde el equipo donde se ejecuta Home Assistant**. Si usas
Tailscale, ese equipo debe tener acceso a la tailnet y poder resolver el nombre del servidor.
El cliente verifica los certificados HTTPS y no sigue redirecciones con la clave.

La clave se guarda en la configuración privada de Home Assistant. Nunca se introduce en
archivos del repositorio, YAML público ni código Python. El servidor guarda únicamente su
hash SHA-256 y un prefijo identificativo. La clave existente de HA que utiliza el servidor
para recoger lecturas es otra credencial, para la dirección contraria.

El servidor concede `connection:read`, `cameras:read`, `lots:write`, `movements:write`,
`consignas:write`, `consignas:confirm`, `consignas:edit`, `consignas:finish`,
`movements:read` y `movements:edit`. Incluye lecturas, altas y correcciones operativas,
sin borrados ni edición de planes. La clave no abre sesiones
Better Auth, no permite gestionar otras claves y no da acceso al visor de base de datos.
Las claves existentes obtienen estas capacidades al actualizar el servidor.
Una cuenta de la aplicación se configura una sola vez en HA; cambiar de clave conserva las entidades.

## Estado y mantenimiento

La entidad **Conexión** muestra conectado tras una consulta autenticada correcta.
Si falla la consulta, queda **no disponible** hasta que se recupere la conexión.
Sus atributos muestran la versión de API y las capacidades anunciadas por el servidor.
El dispositivo incluye un enlace para abrir la aplicación.

- **Cambiar dirección o clave:** menú de la integración → **Reconfigurar**. Deja la clave
  vacía si quieres conservarla.
- **Revocar:** en la web de la aplicación, pulsa **Revocar** junto a la clave. HA pedirá
  una nueva credencial al detectar el rechazo de autenticación.
- **Desinstalar:** elimina la integración en HA y revoca su clave en la aplicación.
  Eliminar la entrada en HA no borra registros de la aplicación.

## Arquitectura y ampliación

```text
Home Assistant → cliente HTTP Python → API pública /api/v1 → reglas de negocio y base de datos
Cliente web    → cliente HTTP TS     → misma API
```

`api.py` contiene el transporte HTTP y la validación del contrato. No depende de Home Assistant.
`config_flow.py` configura la conexión; `coordinator.py` comparte las consultas entre entidades;
las plataformas presentan los datos de la API. No se accede a SQLite desde esta integración.

El contrato inicial es `GET /api/v1/integration/status`, autenticado con
`Authorization: Bearer <clave>`. Devuelve `application`, `apiVersion`, `name`, `accountId`,
`capabilities` y `status`. `accountId` es una identidad estable de la cuenta, independiente de
la URL y de las rotaciones de clave. Nuevas capacidades se implementarán en la API y después
se expondrán como acciones o entidades de HA; no se acepta una ruta HTTP arbitraria desde acciones.

El estado de cámaras se consulta con `GET /api/v1/integration/dashboard`. Las altas
usan los mismos endpoints públicos de lotes, movimientos y consignas que la web.
`GET /api/v1/lotes/:id/opciones-movimiento?fecha=YYYY-MM-DD` devuelve las porciones
disponibles para esa fecha; HA no reconstruye ni valida genealogías.
Al corregir, `movimientoId` limita esas porciones al instante anterior a la operación,
respetando su orden dentro del día. El servidor revalida también los movimientos posteriores.
Las ediciones usan `PUT` sobre el movimiento o consigna identificados, con revisión optimista.
`POST /api/v1/ubicaciones/:id/consignas/:consignaId/finalizar` recibe `{ revision }`
y termina únicamente una manual vigente, usando la hora del servidor y conservando el historial.
Requiere el servidor padre actualizado con estas capacidades; no necesita migraciones nuevas.

## Confirmaciones y notificaciones

La API conserva la última automática confirmada mientras una propuesta nueva está pendiente.
Sin primera confirmación no hay automática vigente. Una consigna manual domina durante su
intervalo; al terminar vuelve la última automática confirmada, con propuesta pendiente si
el plan actual difiere. Si la cámara queda vacía, faltan reglas o hay conflicto, la propuesta
es retirar la automática y también requiere confirmación. No se envían órdenes a actuadores.

La propuesta tiene un UUID propio. Tarjeta, botón, notificación y web confirman mediante
`POST /api/v1/ubicaciones/:id/confirmacion` con `{ "propuestaId": "..." }`. El servidor
recalcula dentro de una transacción y rechaza propuestas obsoletas con HTTP 409.
El servidor también detecta propuestas y vencimientos en segundo plano, sin depender del
sondeo de HA. Las reglas y destinos se configuran en **Notificaciones** de la aplicación
principal; Restate ejecuta los avisos y reintentos durables. El servidor llama directamente
a los servicios `notify` de HA o a `persistent_notification.create` usando su conexión HA.
La antigua opción local `notify_service` ya no se utiliza. Antes de actualizar esta
integración, despliega el servidor con Restate y crea las reglas de propuesta que necesites.

Esta integración solo reenvía las acciones móviles `rcn:<servidor>:<notificación>:<acción>`
a `POST /api/v1/notifications/:id/actions`. Solo admite administradores activos de HA,
comprobados mediante el contexto autenticado del evento móvil y de nuevo antes del envío.
Guarda cada respuesta en `.storage/registros_camaras.<entry_id>.notification_relay`
con escritura atómica y `fsync` **antes** de enviarla; los fallos de escritura se propagan
y no dejan una respuesta sin guardar disponible para el envío. Conserva el UUID durante
los reintentos y reanuda la cola tras reiniciar HA.

La captura se registra antes de conectar con la API y usa la última identidad verificada
guardada en disco, incluso mientras la entrada espera conexión o reautenticación.
Un único propietario de la cola vive durante toda la ejecución de HA, también durante
recargas, para evitar escrituras de instantáneas desactualizadas. Cada respuesta conserva
su ámbito de servidor: solo se envía si una consulta autenticada reciente anuncia ese mismo
ámbito y cuenta. Si cambia el servidor o deja de anunciarlo, las respuestas anteriores se
conservan sin enviarlas. Una clave revocada inicia reautenticación sin perder la cola.

Reintenta cada 30 segundos con hasta 50 notificaciones independientes por pasada y turno
rotativo; un fallo transitorio no bloquea respuestas de otras notificaciones. Las respuestas
de una misma notificación conservan su orden. Incluye el archivo de cola en las copias de HA.
El servidor decide permisos, vigencia de la propuesta y deduplicación. Un rechazo definitivo
se muestra como notificación persistente. Los avisos persistentes sin botones se responden
desde la web. Acusar recibo no recupera una alarma; no confirmar no modifica una consigna.
Aceptar un envío en HA tampoco demuestra su entrega o lectura en un teléfono.

Las tarjetas hablan con el puente WebSocket de la integración, no directamente con la
API. El puente tiene una lista cerrada de operaciones y no expone la clave ni acepta URLs.

## Código fuente y publicación

Este repositorio es la única fuente de la integración y su distribución para HACS.
Se desarrolla directamente en `custom_components/registros_camaras`; no se exporta
desde el repositorio padre. El servidor y sus reglas de negocio pertenecen a
[registros_camaras](https://github.com/Siroguh/registros_camaras), y las tarjetas a
[registros_camaras_cards](https://github.com/Siroguh/registros_camaras_cards).
No contiene la base de datos, claves ni configuración local del proyecto.

Para publicar una actualización, revisa el diff, actualiza la versión de
`custom_components/registros_camaras/manifest.json` y publica una release con el mismo tag.

Comprobaciones estáticas desde este directorio:

```bash
sh scripts/setup-hooks.sh
sh scripts/check.sh
```

El último comando genera `dist/registros_camaras.zip` para instalación manual.
La instalación configura Ruff, pre-commit y Gitleaks con versiones fijadas.
Consulta [CI, hooks y Pullfrog](.github/ci.md) para el flujo de PRs.
HACS instala desde los archivos del repositorio correspondientes a la release.
La CI ejecuta comprobaciones estáticas Python, Hassfest y HACS; no ejecuta tests.

## Licencia

Copyright (C) 2026 Siroguh. Este cliente se distribuye bajo **GPL-3.0-only**,
GNU General Public License versión 3. Consulta [LICENSE](LICENSE).
