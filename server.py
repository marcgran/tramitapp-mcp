#!/usr/bin/env python3
"""
Servidor MCP para TramitApp
===========================

Expone la API de TramitApp como herramientas (tools) MCP para usarlas desde
Claude Desktop (u otro cliente MCP) por transporte stdio.

Configuración por variables de entorno:
    TRAMITAPP_BASE_URL    URL base de la API     (def. https://rrhh.tramitapp.com)
    TRAMITAPP_API_TOKEN   token / api key de tu cuenta
    TRAMITAPP_AUTH_MODE   cómo se envía el token: "header" | "bearer"   (def. header)
    TRAMITAPP_AUTH_HEADER nombre de cabecera en modo "header"           (def. auth)
    TRAMITAPP_TIMEOUT     segundos de timeout                           (def. 30)
    TRAMITAPP_EMPRESA_ID  empresa por defecto (opcional) — _id o nombre; cada tool
                          acepta además un parámetro `empresa` que la sobreescribe

Multiempresa: casi todos los endpoints van scoped como /tramitapi/{company_id}/...
Cada tool acepta `empresa` (nombre, p. ej. "MiEmpresa", o _id). El nombre se
resuelve contra GET /tramitapi/companies (con caché en memoria). Si no se
indica empresa ni hay TRAMITAPP_EMPRESA_ID, y el token solo accede a una
sociedad, se usa esa; con varias, se devuelve un error con las disponibles.

TramitApp autentica con cabecera personalizada:  auth: TOKEN
(equivale al curl que envían:  curl -H 'auth: TOKEN' ...)
El modo por defecto es "header" con cabecera "auth"; el token viaja tal cual,
sin prefijo "Bearer".

Rutas confirmadas contra la especificación OpenAPI oficial
(https://rrhh.tramitapp.com/tramitapp-api.json — copia en docs/tramitapp-api.json).
"""

from __future__ import annotations

import os
import logging
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP

# --------------------------------------------------------------------------- #
# Configuración
# --------------------------------------------------------------------------- #
BASE_URL    = os.environ.get("TRAMITAPP_BASE_URL", "https://rrhh.tramitapp.com").rstrip("/")
API_TOKEN   = os.environ.get("TRAMITAPP_API_TOKEN", "")
AUTH_MODE   = os.environ.get("TRAMITAPP_AUTH_MODE", "header").lower()
AUTH_HEADER = os.environ.get("TRAMITAPP_AUTH_HEADER", "auth")
TIMEOUT     = float(os.environ.get("TRAMITAPP_TIMEOUT", "30"))
EMPRESA_DEF = os.environ.get("TRAMITAPP_EMPRESA_ID", "")

# Rutas reales de la API (confirmadas contra docs/tramitapp-api.json).
# {company_id} lo interpola _request() a partir del parámetro `empresa`.
PATHS = {
    "companies":  "/tramitapi/companies",
    "company":    "/tramitapi/companies/{id}",
    "empleados":  "/tramitapi/{company_id}/employees",
    "empleado":   "/tramitapi/{company_id}/employees/{id}",
    "horas":      "/tramitapi/{company_id}/hours",
    "ausencias":  "/tramitapi/{company_id}/absences",
    "turnos":     "/tramitapi/{company_id}/shifts",
    "clocking":   "/tramitapi/{company_id}/clocking",
    "documentos": "/tramitapi/{company_id}/documents",
    "vacaciones": "/tramitapi/{company_id}/vacations",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [tramitapp-mcp] %(message)s")
log = logging.getLogger("tramitapp-mcp")

mcp = FastMCP("tramitapp")


# --------------------------------------------------------------------------- #
# Cliente HTTP
# --------------------------------------------------------------------------- #
def _auth_headers() -> dict[str, str]:
    """Construye las cabeceras de autenticación según el modo configurado."""
    if not API_TOKEN:
        log.warning("TRAMITAPP_API_TOKEN vacío: las llamadas fallarán con 401.")
        return {}
    if AUTH_MODE == "bearer":
        return {"Authorization": f"Bearer {API_TOKEN}"}
    return {AUTH_HEADER: API_TOKEN}


async def _http(
    method: str,
    path: str,
    *,
    params: Optional[dict[str, Any]] = None,
    json: Optional[Any] = None,
) -> Any:
    """Llamada HTTP cruda con manejo de errores uniforme."""
    url = f"{BASE_URL}{path}"
    headers = {"Accept": "application/json", **_auth_headers()}
    if json is not None:
        headers["Content-Type"] = "application/json"

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.request(method, url, params=params, json=json, headers=headers)
    except httpx.RequestError as exc:
        return {"error": "network_error", "detail": str(exc), "url": url}

    if resp.status_code == 401:
        return {"error": "unauthorized", "detail": "Token inválido o ausente. Revisa TRAMITAPP_API_TOKEN / TRAMITAPP_AUTH_MODE."}
    if resp.status_code == 404:
        return {"error": "not_found", "detail": f"Ruta no encontrada: {path}.", "status": 404}
    if resp.status_code >= 400:
        return {"error": "http_error", "status": resp.status_code, "detail": resp.text[:1000]}

    if not resp.content:
        return {"ok": True, "status": resp.status_code}
    try:
        return resp.json()
    except ValueError:
        return {"raw": resp.text[:2000]}


# --------------------------------------------------------------------------- #
# Resolución de empresa (multiempresa)
# --------------------------------------------------------------------------- #
_EMPRESAS_CACHE: Optional[list[dict[str, Any]]] = None


async def _empresas() -> Any:
    """Lista de sociedades accesibles, cacheada en memoria para la sesión."""
    global _EMPRESAS_CACHE
    if _EMPRESAS_CACHE is None:
        data = await _http("GET", PATHS["companies"])
        if not isinstance(data, list):
            return data  # dict de error
        _EMPRESAS_CACHE = data
    return _EMPRESAS_CACHE


def _parece_id(valor: str) -> bool:
    """Los _id de TramitApp son ObjectId de Mongo: 24 caracteres hex."""
    return len(valor) == 24 and all(c in "0123456789abcdef" for c in valor.lower())


async def _empresa_id(empresa: Optional[str]) -> Any:
    """Resuelve `empresa` (nombre o _id) al _id de la sociedad.

    Devuelve el _id como str, o un dict {"error": ...} si no se puede resolver.
    Orden: parámetro `empresa` > TRAMITAPP_EMPRESA_ID > única empresa del token.
    """
    if not empresa and EMPRESA_DEF:
        empresa = EMPRESA_DEF

    if empresa and _parece_id(empresa):
        return empresa

    data = await _empresas()
    if not isinstance(data, list):
        return data
    disponibles = [{"_id": c.get("_id"), "name": c.get("name")} for c in data]

    if not empresa:
        if len(data) == 1:
            return data[0]["_id"]
        return {
            "error": "empresa_requerida",
            "detail": "El token accede a varias sociedades; indica el parámetro `empresa` (nombre o _id).",
            "disponibles": disponibles,
        }

    exactas = [c for c in data if (c.get("name") or "").lower() == empresa.lower()]
    if not exactas:
        exactas = [c for c in data if empresa.lower() in (c.get("name") or "").lower()]
    if len(exactas) == 1:
        return exactas[0]["_id"]
    return {
        "error": "empresa_ambigua" if exactas else "empresa_desconocida",
        "detail": f"'{empresa}' no identifica una única sociedad.",
        "disponibles": disponibles,
    }


async def _request(
    method: str,
    path: str,
    *,
    params: Optional[dict[str, Any]] = None,
    json: Optional[Any] = None,
    empresa: Optional[str] = None,
) -> Any:
    """Llamada a la API resolviendo {company_id} en el path si procede."""
    if "{company_id}" in path:
        cid = await _empresa_id(empresa)
        if isinstance(cid, dict):
            return cid
        path = path.replace("{company_id}", cid)
    return await _http(method, path, params=params, json=json)


def _filtrar_por_empleado(data: Any, empleado_id: Optional[str]) -> Any:
    """La API no filtra por empleado en los listados; se filtra en cliente."""
    if empleado_id and isinstance(data, list):
        return [x for x in data if isinstance(x, dict) and x.get("employees_id") == empleado_id]
    return data


# --------------------------------------------------------------------------- #
# Tools de lectura
# --------------------------------------------------------------------------- #
@mcp.tool()
async def listar_empresas() -> Any:
    """Lista las sociedades/empresas a las que tiene acceso el token.

    El resto de tools aceptan un parámetro `empresa` con el nombre o el _id
    de cualquiera de estas sociedades.
    """
    return await _empresas()


@mcp.tool()
async def listar_empleados(
    empresa: Optional[str] = None,
    modified_since: Optional[str] = None,
    columns: Optional[str] = None,
    include: Optional[str] = None,
) -> Any:
    """Lista todos los empleados de una sociedad (sin paginación).

    Args:
        empresa: nombre o _id de la sociedad (p. ej. "MiEmpresa"). Opcional si hay
            empresa por defecto configurada o el token solo accede a una.
        modified_since: timestamp — solo devuelve modificados desde esa fecha (actualización incremental).
        columns: campos a proyectar separados por comas, p. ej. "_id,firstName,lastName".
        include: campos de parametrización opcionales, p. ej. "locations,positions,skills,projects".
    """
    params: dict[str, Any] = {}
    if modified_since:
        params["modified_since"] = modified_since
    if columns:
        params["columns"] = columns
    if include:
        params["include"] = include
    return await _request("GET", PATHS["empleados"], params=params or None, empresa=empresa)


@mcp.tool()
async def obtener_empleado(empleado_id: str, empresa: Optional[str] = None) -> Any:
    """Obtiene el detalle completo de un empleado por su ID (_id).

    Args:
        empleado_id: _id del empleado.
        empresa: nombre o _id de la sociedad (p. ej. "MiEmpresa").
    """
    return await _request("GET", PATHS["empleado"].replace("{id}", empleado_id), empresa=empresa)


@mcp.tool()
async def listar_fichajes(
    mes_desde: str,
    mes_hasta: str,
    empresa: Optional[str] = None,
    empleado_id: Optional[str] = None,
) -> Any:
    """Consulta los fichajes / imputaciones de horas en un rango de MESES.

    Args:
        mes_desde: mes inicial en formato YYYY-MM (p. ej. 2026-01).
        mes_hasta: mes final en formato YYYY-MM.
        empresa: nombre o _id de la sociedad (p. ej. "MiEmpresa").
        empleado_id: opcional — filtra por el _id del empleado (filtrado en cliente,
            la API devuelve siempre todos los empleados).
    """
    params = {"start": mes_desde, "end": mes_hasta}
    data = await _request("GET", PATHS["horas"], params=params, empresa=empresa)
    return _filtrar_por_empleado(data, empleado_id)


@mcp.tool()
async def listar_ausencias(
    fecha_desde: str,
    fecha_hasta: str,
    empresa: Optional[str] = None,
    empleado_id: Optional[str] = None,
) -> Any:
    """Lista ausencias, vacaciones y bajas en un rango de fechas.

    Args:
        fecha_desde: día inicial en formato YYYY-MM-DD.
        fecha_hasta: día final en formato YYYY-MM-DD.
        empresa: nombre o _id de la sociedad (p. ej. "MiEmpresa").
        empleado_id: opcional — filtra por el _id del empleado (filtrado en cliente).
    """
    params = {"start": fecha_desde, "end": fecha_hasta}
    data = await _request("GET", PATHS["ausencias"], params=params, empresa=empresa)
    return _filtrar_por_empleado(data, empleado_id)


@mcp.tool()
async def listar_turnos(
    mes_desde: str,
    mes_hasta: str,
    empresa: Optional[str] = None,
    empleado_id: Optional[str] = None,
) -> Any:
    """Lista la planificación de jornadas y turnos en un rango de MESES.

    Args:
        mes_desde: mes inicial en formato YYYY-MM (p. ej. 2026-01).
        mes_hasta: mes final en formato YYYY-MM.
        empresa: nombre o _id de la sociedad (p. ej. "MiEmpresa").
        empleado_id: opcional — filtra por el _id del empleado (filtrado en cliente).
    """
    params = {"start": mes_desde, "end": mes_hasta}
    data = await _request("GET", PATHS["turnos"], params=params, empresa=empresa)
    return _filtrar_por_empleado(data, empleado_id)


@mcp.tool()
async def saldo_vacaciones(empresa: Optional[str] = None) -> Any:
    """Consulta el saldo de vacaciones de los empleados de una sociedad.

    Args:
        empresa: nombre o _id de la sociedad (p. ej. "MiEmpresa").
    """
    return await _request("GET", PATHS["vacaciones"], empresa=empresa)


# --------------------------------------------------------------------------- #
# Tools de escritura  [MODIFICA DATOS — probar siempre en cuenta demo primero]
# --------------------------------------------------------------------------- #
@mcp.tool()
async def crear_fichaje(
    empleado_id: str,
    fecha_hora: str,
    entrada: bool,
    empresa: Optional[str] = None,
    tz: str = "Europe/Madrid",
) -> Any:
    """Crea un fichaje automático de entrada o salida.  [MODIFICA DATOS]

    Args:
        empleado_id: _id del empleado.
        fecha_hora: fecha y hora del fichaje, p. ej. 2026-06-18T08:30:00.
        entrada: True para fichaje de entrada, False para salida.
        empresa: nombre o _id de la sociedad (p. ej. "MiEmpresa").
        tz: zona horaria del fichaje (def. Europe/Madrid).
    """
    payload = {"employees_id": empleado_id, "date": fecha_hora, "tz": tz, "in": entrada}
    return await _request("POST", PATHS["clocking"], json=payload, empresa=empresa)


# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    mcp.run()  # transporte stdio por defecto
