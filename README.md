# TramitApp MCP

Servidor **MCP (Model Context Protocol)** que expone la API de [TramitApp](https://tramitapp.com) como herramientas para **Claude Desktop** (y cualquier cliente MCP compatible). Permite consultar y registrar datos de RR.HH. —fichajes, ausencias, turnos, vacaciones y empleados— directamente desde el chat con Claude.

Con soporte **multiempresa**: si tu token accede a varias sociedades, cada herramienta acepta la empresa por nombre o ID.

---

## ¿Qué hace?

Claude puede responder preguntas como:

- *"¿Qué empresas gestionamos en TramitApp?"*
- *"Muéstrame las ausencias de agosto en MiEmpresa"*
- *"¿Cuántos empleados tiene MiEmpresa?"*
- *"¿Cuál es el saldo de vacaciones?"*

sin salir del chat, llamando directamente a la API de TramitApp.

---

## Herramientas incluidas

| Herramienta | Tipo | Descripción |
|-------------|------|-------------|
| `listar_empresas` | lectura | Sociedades a las que accede el token (nombre y `_id`) |
| `listar_empleados` | lectura | Todos los empleados de una sociedad (`modified_since`, `columns`, `include`) |
| `obtener_empleado` | lectura | Detalle completo de un empleado por ID |
| `listar_fichajes` | lectura | Fichajes / horas por rango de **meses** (`YYYY-MM`) |
| `listar_ausencias` | lectura | Ausencias, vacaciones y bajas por rango de **días** (`YYYY-MM-DD`) |
| `listar_turnos` | lectura | Jornadas y turnos por rango de **meses** (`YYYY-MM`) |
| `saldo_vacaciones` | lectura | Saldo de vacaciones de los empleados |
| `crear_fichaje` | **escritura** | Crea un fichaje de entrada o salida (`/clocking`) |

Todas las herramientas con ámbito de empresa aceptan un parámetro opcional `empresa` (nombre como `"MiEmpresa"` — sin distinguir mayúsculas — o el `_id` de 24 caracteres). Si el token solo accede a una sociedad, no hace falta indicarla.

---

## Requisitos

- Python 3.10 o superior
- Cuenta en TramitApp con acceso a la API (token)
- [Claude Desktop](https://claude.ai/download)

---

## Instalación

### 1. Clona el repositorio

```bash
git clone https://github.com/TU_USUARIO/tramitapp-mcp.git
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

Copia `.env.example` a `.env` y rellena tu token:

```bash
cp .env.example .env
# Edita .env y pon tu TRAMITAPP_API_TOKEN
```

> **Token demo vs. producción**: TramitApp entrega primero un token de cuenta demo para pruebas. Al hacer la integración real te proporcionan un token nuevo. No intercambies uno por otro sin actualizar la config.

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

---

## Probar el servidor de forma aislada

Antes de configurar Claude Desktop puedes probar el servidor con el inspector MCP (abre una UI web):

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
| `PATHS` | Todas las rutas en un solo sitio. |

Toda herramienta nueva debe pasar por `_request()` y marcar `[MODIFICA DATOS]` en su docstring si escribe datos.

---

## Dependencias

| Paquete | Versión | Uso |
|---------|---------|-----|
| `mcp` | >=1.2.0, <2 | SDK oficial MCP + FastMCP (API 1.x) |
| `httpx` | >=0.27.0 | Cliente HTTP asíncrono |

---

## Licencia

MIT
