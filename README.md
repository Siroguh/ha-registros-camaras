# Registros Cámaras para Home Assistant

Cliente de la API REST de **Registros Cámaras**, instalable como integración personalizada
de HACS. Requiere Home Assistant **2026.10 o posterior** y el servidor de Registros Cámaras
con soporte para claves de integración (migración `0017`).

## Qué incluye esta versión

- Configuración desde **Ajustes → Dispositivos y servicios → Añadir integración**.
- Conexión con la URL de la aplicación y una clave de integración propia.
- Una entidad de diagnóstico **Conexión**, actualizada cada 60 segundos.
- Reintento de conexión, reautenticación si se revoca la clave y reconfiguración de la URL.
- Diagnósticos sin claves, direcciones del servidor ni datos personales.
- Interfaz en español e inglés.

Esta entrega es la base de conexión. Todavía no ofrece acciones para crear lotes,
registrar movimientos, cambiar consignas ni confirmar cambios automáticos.

## Instalar con HACS

1. En **HACS → menú de tres puntos → Repositorios personalizados**, añade:
   `https://github.com/Siroguh/ha-registros-camaras`.
2. Selecciona el tipo **Integración**.
3. Busca **Registros Cámaras** en HACS e instálala.
4. Reinicia Home Assistant.
5. En **Ajustes → Dispositivos y servicios → Añadir integración**, busca **Registros Cámaras**.

El repositorio se añade como personalizado; no es necesario que figure en el catálogo general.
HACS gestiona los archivos del cliente y sus actualizaciones, no el servidor de la aplicación.

Para instalar manualmente, copia `custom_components/registros_camaras` a la carpeta
`config/custom_components/registros_camaras` de Home Assistant y reinícialo.

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

Esta versión concede exclusivamente `connection:read`. La clave no abre sesiones Better Auth,
no permite gestionar otras claves y no da acceso al visor de la base de datos ni a escrituras.
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

La siguiente entrega prevista abarca lotes, movimientos completos o partidos con fecha,
consignas manuales y confirmación de cambios automáticos. La decisión acordada es mantener
la última consigna confirmada mientras la nueva está pendiente. Ese estado y su confirmación
pertenecerán al servidor, para compartirlos entre HA y la web; esta versión no modifica todavía
el cálculo de consignas existente.

## Código fuente y publicación

La fuente se mantiene en `apps/home-assistant` del proyecto
[registros_camaras](https://github.com/Siroguh/registros_camaras).
Este repositorio es su distribución independiente para HACS. No contiene el servidor,
la base de datos, claves ni configuración local del proyecto.

Desde el monorepo se exporta con:

```bash
pnpm ha:export /ruta/a/un/directorio-nuevo
```

El exportador usa una lista explícita de archivos y exige un destino nuevo. Para publicar
una actualización, aplica el contenido exportado en el repositorio de distribución,
revisa el diff y publica una release cuyo tag coincida con `manifest.json`.

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
