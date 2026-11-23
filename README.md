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
Los campos se muestran en la interfaz de acciones de HA y se pueden usar en automatizaciones.
Los controles interactivos requieren un administrador de HA; las automatizaciones locales
también pueden llamar a las acciones. Todas las operaciones se validan en la API.

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
`consignas:write` y `consignas:confirm`. Incluye lecturas necesarias y altas operativas,
no borrados, edición de planes ni correcciones históricas. La clave no abre sesiones
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

## Confirmaciones y notificaciones

La API conserva la última automática confirmada mientras una propuesta nueva está pendiente.
Sin primera confirmación no hay automática vigente. Una consigna manual domina durante su
intervalo; al terminar vuelve la última automática confirmada, con propuesta pendiente si
el plan actual difiere. Si la cámara queda vacía, faltan reglas o hay conflicto, la propuesta
es retirar la automática y también requiere confirmación. No se envían órdenes a actuadores.

La propuesta tiene un UUID propio. Tarjeta, botón, notificación y web confirman mediante
`POST /api/v1/ubicaciones/:id/confirmacion` con `{ "propuestaId": "..." }`. El servidor
recalcula dentro de una transacción y rechaza propuestas obsoletas con HTTP 409.
Las propuestas se detectan en las consultas (HA sondea cada 60 segundos).

En **Ajustes → Dispositivos y servicios → Registros Cámaras → Configurar** puedes elegir
un servicio `notify.mobile_app_…` para notificaciones accionables o avisos persistentes
dentro de HA. Por defecto están desactivadas. Las notificaciones móviles incluyen
**Confirmar esta propuesta**; los avisos persistentes remiten al botón o tarjeta.
Una propuesta se notifica una vez mientras permanece pendiente durante esa carga de la
integración; reiniciar o recargar HA puede volver a notificarla. Una notificación antigua
nunca confirma silenciosamente una propuesta nueva.

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
uvx ruff check .
uvx ruff format --check .
python3 -m compileall -q custom_components package.py
python3 package.py
```

El último comando genera `dist/registros_camaras.zip` para instalación manual.
HACS instala desde los archivos del repositorio correspondientes a la release.
La CI ejecuta comprobaciones estáticas Python, Hassfest y HACS; no ejecuta tests.

## Licencia

Copyright (C) 2026 Siroguh. Este cliente se distribuye bajo **GPL-3.0-only**,
GNU General Public License versión 3. Consulta [LICENSE](LICENSE).
