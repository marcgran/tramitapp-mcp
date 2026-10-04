# TramitApp MCP

Servidor **MCP (Model Context Protocol)** que expone la API de [TramitApp](https://tramitapp.com) como herramientas para **Claude Desktop** (y cualquier cliente MCP compatible). Permite consultar y registrar datos de RR.HH. —fichajes, ausencias, turnos, vacaciones y empleados— directamente desde el chat con Claude.

Con soporte **multiempresa**: si tu token accede a varias sociedades, cada herramienta acepta la empresa por nombre o ID.

---

## ¿Qué hace?

Claude puede responder preguntas como:

- *"¿Qué empresas gestionamos en TramitApp?"*
- *"Muéstrame las ausencias de agosto en MiEmpresa"*
- *"¿Cuántas horas ha fichado cada persona este mes?"*
- *"¿Qué fichajes tiene Juan García en septiembre?"*
- *"¿Cuál es el saldo de vacaciones?"*

sin salir del chat, llamando directamente a la API de TramitApp.

---

## Herramientas incluidas

| Herramienta | Tipo | Descripción |
|-------------|------|-------------|
| `listar_empresas` | lectura | Sociedades a las que accede el token (nombre y `_id`) |
| `buscar_empleado` | lectura | Busca por nombre, apellidos, DNI/NIE o email y devuelve el `_id` (sin acentos ni mayúsculas) |
| `listar_empleados` | lectura | Empleados de una sociedad, por defecto **solo campos no sensibles** (`columns`, `modified_since`, `include`) |
| `obtener_empleado` | lectura | Ficha completa de un empleado por `_id` (incluye datos personales) |
| `listar_fichajes` | lectura | Fichajes por rango de **meses** (`YYYY-MM`): **resumen por empleado**, o detalle si se indica `empleado_id` |
| `listar_ausencias` | lectura | Ausencias por rango de **días** (`YYYY-MM-DD`), filtrables por `estado` (por defecto, aprobadas + pendientes) |
| `listar_turnos` | lectura | Jornadas y turnos por rango de **meses** (`YYYY-MM`) |
| `saldo_vacaciones` | lectura | Saldo de vacaciones de los empleados |
| `crear_fichaje` | **escritura** | Crea un fichaje de entrada o salida (`/clocking`) |

Todas las herramientas con ámbito de empresa aceptan un parámetro opcional `empresa` (nombre como `"MiEmpresa"` — sin distinguir mayúsculas — o el `_id` de 24 caracteres). Si el token solo accede a una sociedad, no hace falta indicarla.

### Decisiones de diseño

- **Privacidad**: `listar_empleados` devuelve por defecto `_id`, nombre, email corporativo, categoría, centro y fechas de contrato. IBAN, NSS, DNI, fecha de nacimiento o discapacidad solo llegan al chat si se piden de forma explícita (`columns` u `obtener_empleado`).
- **Tamaño de las respuestas**: el detalle de fichajes de una plantilla puede superar los 300.000 caracteres al mes, más de lo que cabe en el contexto de Claude. Por eso `listar_fichajes` sin `empleado_id` devuelve un resumen por empleado (fichajes, horas aprobadas, horas pendientes, rechazados/cancelados).
- **Validación previa**: los formatos de fecha y los `_id` se comprueban antes de llamar a la API, porque con un formato incorrecto la API devuelve una lista vacía sin avisar.

---

## Requisitos

- Python 3.10 o superior
- Cuenta en TramitApp con acceso a la API (token)
- [Claude Desktop](https://claude.ai/download)

---

## Instalación

### 1. Clona el repositorio

```bash
git clone https://github.com/marcgran/tramitapp-mcp.git
cd tramitapp-mcp
```

### 2. Crea el entorno virtual e instala dependencias

```bash
# Windows
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> El código usa la API 1.x del SDK (`FastMCP`); `requirements.txt` ya fija `mcp<2`.

### 3. Configura el token

Copia `.env.example` a `.env` y rellena tu token. `server.py` lee el `.env` automáticamente; si una variable está definida también en el entorno (por ejemplo en la configuración de Claude Desktop), tiene prioridad la del entorno.

```bash
cp .env.example .env
# Edita .env y pon tu TRAMITAPP_API_TOKEN
```

> La clave de API se solicita al soporte de TramitApp (chat de la aplicación o soporte@tramitapp.com).

---

## Configuración en Claude Desktop

Edita el archivo de configuración de Claude Desktop:

- **Windows**: `C:\Users\TU_USUARIO\AppData\Roaming\Claude\claude_desktop_config.json`
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`

Añade el servidor dentro de `mcpServers` (ajusta las rutas absolutas a tu instalación):

```json
{
  "mcpServers": {
    "tramitapp": {
      "command": "C:\\ruta\\a\\tramitapp-mcp\\.venv\\Scripts\\python.exe",
      "args": ["C:\\ruta\\a\\tramitapp-mcp\\server.py"],
      "env": {
        "TRAMITAPP_API_TOKEN": "tu_token_aqui",
        "TRAMITAPP_AUTH_MODE": "header"
      }
    }
  }
}
```

En macOS/Linux el comando sería `/ruta/a/tramitapp-mcp/.venv/bin/python`.

Reinicia Claude Desktop. Verás las herramientas de `tramitapp` disponibles en el chat.

---

## Autenticación

TramitApp autentica con una **cabecera personalizada** (confirmado):

```
auth: TOKEN
```

El token viaja tal cual, **sin prefijo `Bearer`**. El servidor ya está configurado así por defecto (`TRAMITAPP_AUTH_MODE=header`, `TRAMITAPP_AUTH_HEADER=auth`).

Variables de entorno disponibles:

| Variable | Por defecto | Descripción |
|----------|-------------|-------------|
| `TRAMITAPP_BASE_URL` | `https://rrhh.tramitapp.com` | URL base de la API |
| `TRAMITAPP_API_TOKEN` | *(vacío)* | Token de autenticación |
| `TRAMITAPP_AUTH_MODE` | `header` | `header` o `bearer` |
| `TRAMITAPP_AUTH_HEADER` | `auth` | Nombre de cabecera (modo `header`) |
| `TRAMITAPP_TIMEOUT` | `30` | Timeout en segundos |
| `TRAMITAPP_EMPRESA_ID` | *(vacío)* | Empresa por defecto opcional (nombre o `_id`); el parámetro `empresa` de cada herramienta la sobreescribe |

---

## La API de TramitApp

Rutas confirmadas contra la especificación OpenAPI oficial (copia en [`docs/tramitapp-api.json`](docs/tramitapp-api.json); Swagger legible en `https://rrhh.tramitapp.com/doc`):

- Base real: **`/tramitapi`**. Casi todos los endpoints van scoped por sociedad: `/tramitapi/{company_id}/...`
- El `company_id` sale de `GET /tramitapi/companies` (herramienta `listar_empresas`).
- **Sin paginación**. Rangos de fechas con `start`/`end`: días (`YYYY-MM-DD`) en `absences`, meses (`YYYY-MM`) en `hours` y `shifts`.
- La API no filtra por empleado en los listados; el servidor filtra en cliente por `employees_id`.
- `columns` solo funciona como clave repetida (`columns=_id&columns=firstName`); separado por comas devuelve objetos vacíos. El servidor acepta comas y lo convierte.
- No hay 401: con un token incorrecto la API responde 403, y sin token 422 `access-denied`.
- Un empleado inexistente devuelve `[]` en lugar de 404.

---

## Probar el servidor de forma aislada

Antes de configurar Claude Desktop puedes probar el servidor con el inspector MCP (abre una UI web; necesita [Node.js](https://nodejs.org) instalado):

```bash
mcp dev server.py
```

---

## Arquitectura

`server.py` es un único archivo plano por capas:

| Capa | Función |
|------|---------|
| `_auth_headers()` | Única lógica de autenticación — no duplicar. |
| `_http()` | HTTP crudo — devuelve `{"error": "..."}` en caso de fallo, nunca lanza excepciones. |
| `_request()` | Lo que llaman las herramientas — resuelve `{company_id}` y delega en `_http()`. |
| `_empresa_id()` | Resolución multiempresa: parámetro `empresa` > `TRAMITAPP_EMPRESA_ID` > única empresa del token. Nombres resueltos contra `GET /companies`, con caché. |
| `_error_rango()`, `_error_empleado_id()` | Validación de fechas y `_id` antes de llamar a la API. |
| `PATHS` | Todas las rutas en un solo sitio. |

Toda herramienta nueva debe pasar por `_request()` y marcar `[MODIFICA DATOS]` en su docstring si escribe datos.

---

## Dependencias

| Paquete | Versión | Uso |
|---------|---------|-----|
| `mcp[cli]` | >=1.2.0, <2 | SDK oficial MCP + FastMCP (API 1.x); `[cli]` aporta `mcp dev` |
| `httpx` | >=0.27.0 | Cliente HTTP asíncrono |

---

## Licencia

MIT
