#!/usr/bin/env python3
"""
Servidor MCP para TramitApp
===========================

Expone la API de TramitApp como herramientas (tools) MCP para usarlas desde
Claude Desktop (u otro cliente MCP) por transporte stdio.

Configuración por variables de entorno (o en un archivo .env junto a este
script; las variables del entorno tienen prioridad sobre el .env):
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
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP


# --------------------------------------------------------------------------- #
# Configuración
# --------------------------------------------------------------------------- #
def _cargar_env(ruta: Path) -> None:
    """Carga un .env sencillo (CLAVE=valor). No pisa variables ya definidas."""
    if not ruta.is_file():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        valor = valor.strip().strip('"').strip("'")
        if valor:
            os.environ.setdefault(clave.strip(), valor)


_cargar_env(Path(__file__).resolve().parent / ".env")

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

# Campos que devuelve listar_empleados si no se pide otra cosa. Deja fuera los
# datos sensibles (IBAN, NSS, DNI, nacimiento, discapacidad, observaciones...):
# para la ficha completa de una persona está obtener_empleado.
COLUMNAS_EMPLEADO = [
    "_id", "firstName", "lastName", "lastName2", "corporate_email", "category",
    "workplace_id", "contract_date", "contract_end_date", "contract_cease_date",
]

# Estados de ausencias/fichajes en la API, con alias en castellano.
ESTADOS = {
    "pending": "pending", "pendiente": "pending", "pendientes": "pending",
    "done": "done", "aprobada": "done", "aprobadas": "done", "aprobado": "done", "aprobados": "done",
    "canceled": "canceled", "cancelada": "canceled", "canceladas": "canceled",
    "rejected": "rejected", "rechazada": "rejected", "rechazadas": "rejected",
}
ESTADOS_VIGENTES = {"done", "pending"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [tramitapp-mcp] %(message)s")
log = logging.getLogger("tramitapp-mcp")

mcp = FastMCP("tramitapp")


# --------------------------------------------------------------------------- #
# Cliente HTTP
# --------------------------------------------------------------------------- #
def _auth_headers() -> dict[str, str]:
    """Construye las cabeceras de autenticación según el modo configurado."""
    if not API_TOKEN:
        log.warning("TRAMITAPP_API_TOKEN vacío: las llamadas serán rechazadas.")
        return {}
    if AUTH_MODE == "bearer":
        return {"Authorization": f"Bearer {API_TOKEN}"}
    return {AUTH_HEADER: API_TOKEN}


async def _http(
    method: str,
    path: str,
    *,
    params: Any = None,
    json: Optional[Any] = None,
) -> Any:
    """Llamada HTTP cruda con manejo de errores uniforme.

    `params` puede ser un dict o una lista de tuplas (para claves repetidas,
    p. ej. columns=_id&columns=firstName).
    """
    url = f"{BASE_URL}{path}"
    headers = {"Accept": "application/json", **_auth_headers()}
    if json is not None:
        headers["Content-Type"] = "application/json"

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.request(method, url, params=params, json=json, headers=headers)
    except httpx.RequestError as exc:
        return {"error": "network_error", "detail": str(exc), "url": url}

    # TramitApp no usa 401: responde 403 con token incorrecto y 422
    # "access-denied" sin token o sin permiso sobre el recurso.
    if resp.status_code in (401, 403) or (resp.status_code == 422 and "access-denied" in resp.text):
        return {
            "error": "unauthorized",
            "status": resp.status_code,
            "detail": "Token inválido, ausente o sin permiso sobre este recurso. Revisa TRAMITAPP_API_TOKEN / TRAMITAPP_AUTH_MODE.",
        }
    if resp.status_code == 404:
        return {"error": "not_found", "status": 404, "detail": f"Recurso no encontrado: {path}."}
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
    params: Any = None,
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


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #
def _error_fecha(valor: str, campo: str, formato: str) -> Optional[dict[str, Any]]:
    """Valida el formato de una fecha. La API no avisa: con un formato
    incorrecto puede devolver una lista vacía como si no hubiera datos."""
    patrones = {"YYYY-MM": "%Y-%m", "YYYY-MM-DD": "%Y-%m-%d"}
    try:
        datetime.strptime(valor, patrones[formato])
    except ValueError:
        return {"error": "fecha_invalida", "detail": f"`{campo}` debe tener formato {formato}; recibido '{valor}'."}
    return None


def _error_rango(desde: str, hasta: str, formato: str, prefijo: str) -> Optional[dict[str, Any]]:
    """Valida ambos extremos de un rango de fechas y su orden.
    `prefijo` es el del nombre del parámetro ("mes" o "fecha")."""
    error = _error_fecha(desde, f"{prefijo}_desde", formato) or _error_fecha(hasta, f"{prefijo}_hasta", formato)
    if error:
        return error
    if desde > hasta:  # mismo formato ISO: el orden de texto es el cronológico
        return {"error": "rango_invalido", "detail": f"La fecha inicial ({desde}) es posterior a la final ({hasta})."}
    return None


def _error_empleado_id(empleado_id: str) -> Optional[dict[str, Any]]:
    """Exige un _id válido: evita filtrar por un nombre (daría vacío) y que un
    valor arbitrario se cuele en la ruta de la URL."""
    if _parece_id(empleado_id):
        return None
    return {
        "error": "empleado_id_invalido",
        "detail": f"'{empleado_id}' no es un _id de empleado (24 caracteres hexadecimales). Usa buscar_empleado para obtenerlo a partir del nombre o el DNI.",
    }


def _columnas(columns: str | list[str]) -> list[tuple[str, str]]:
    """La API solo acepta columnas como clave repetida (columns=a&columns=b);
    separadas por comas devuelve objetos vacíos."""
    if isinstance(columns, str):
        columns = columns.split(",")
    return [("columns", c.strip()) for c in columns if c.strip()]


def _normalizar(texto: str) -> str:
    """Minúsculas y sin acentos, para comparar nombres."""
    texto = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in texto if not unicodedata.combining(c)).lower()


def _nombre_completo(emp: dict[str, Any]) -> str:
    partes = (emp.get("firstName"), emp.get("lastName"), emp.get("lastName2"))
    return " ".join(p for p in partes if p)


async def _nombres_empleados(empresa: Optional[str]) -> Any:
    """Mapa _id -> nombre completo de los empleados, o dict de error."""
    data = await _request(
        "GET", PATHS["empleados"],
        params=_columnas(["_id", "firstName", "lastName", "lastName2"]),
        empresa=empresa,
    )
    if not isinstance(data, list):
        return data
    return {e.get("_id"): _nombre_completo(e) for e in data}


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
async def buscar_empleado(texto: str, empresa: Optional[str] = None) -> Any:
    """Busca empleados por nombre, apellidos, DNI/NIE o email y devuelve su _id.

    Úsala para obtener el _id que necesitan obtener_empleado, crear_fichaje o
    los filtros por empleado. No distingue mayúsculas ni acentos, y todas las
    palabras deben aparecer (p. ej. "juan garcia" encuentra "Juan García Pérez").

    Args:
        texto: nombre, apellidos, DNI/NIE o email (completo o en parte).
        empresa: nombre o _id de la sociedad (p. ej. "MiEmpresa").
    """
    palabras = _normalizar(texto).split()
    if not palabras:
        return {"error": "texto_vacio", "detail": "Indica un nombre, DNI o email a buscar."}

    data = await _request(
        "GET", PATHS["empleados"],
        params=_columnas(["_id", "firstName", "lastName", "lastName2", "nationalId",
                          "email", "corporate_email", "category", "contract_cease_date"]),
        empresa=empresa,
    )
    if not isinstance(data, list):
        return data

    encontrados = []
    for e in data:
        pajar = _normalizar(" ".join(str(e.get(k) or "") for k in
                                     ("firstName", "lastName", "lastName2", "nationalId", "email", "corporate_email")))
        if all(p in pajar for p in palabras):
            # No se devuelven DNI ni email personal: solo sirvieron para buscar.
            encontrados.append({
                "_id": e.get("_id"),
                "nombre": _nombre_completo(e),
                "corporate_email": e.get("corporate_email"),
                "category": e.get("category"),
                "contract_cease_date": e.get("contract_cease_date"),
            })
    return encontrados


@mcp.tool()
async def listar_empleados(
    empresa: Optional[str] = None,
    modified_since: Optional[str] = None,
    columns: Optional[str] = None,
    include: Optional[str] = None,
) -> Any:
    """Lista todos los empleados de una sociedad (sin paginación).

    Por defecto devuelve solo datos no sensibles: _id, nombre y apellidos,
    email corporativo, categoría, centro de trabajo y fechas de contrato
    (contract_cease_date informada = empleado dado de baja). Para la ficha
    completa de una persona usa obtener_empleado.

    Args:
        empresa: nombre o _id de la sociedad (p. ej. "MiEmpresa"). Opcional si hay
            empresa por defecto configurada o el token solo accede a una.
        modified_since: timestamp — solo devuelve modificados desde esa fecha (actualización incremental).
        columns: campos a devolver separados por comas, p. ej. "_id,firstName,lastName".
            Si se omite, se usan los campos no sensibles indicados arriba.
        include: campos de parametrización opcionales, p. ej. "locations,positions,skills,projects".
    """
    params = _columnas(columns or COLUMNAS_EMPLEADO)
    if modified_since:
        params.append(("modified_since", modified_since))
    if include:
        params.append(("include", include))
    return await _request("GET", PATHS["empleados"], params=params, empresa=empresa)


@mcp.tool()
async def obtener_empleado(empleado_id: str, empresa: Optional[str] = None) -> Any:
    """Obtiene la ficha completa de un empleado por su _id (incluye datos personales).

    Args:
        empleado_id: _id del empleado (obtenlo con buscar_empleado).
        empresa: nombre o _id de la sociedad (p. ej. "MiEmpresa").
    """
    error = _error_empleado_id(empleado_id)
    if error:
        return error
    data = await _request("GET", PATHS["empleado"].replace("{id}", empleado_id), empresa=empresa)
    if data in ([], {}):  # la API responde [] en lugar de 404
        return {"error": "not_found", "detail": f"No existe ningún empleado con _id {empleado_id} en esa sociedad."}
    return data


@mcp.tool()
async def listar_fichajes(
    mes_desde: str,
    mes_hasta: str,
    empresa: Optional[str] = None,
    empleado_id: Optional[str] = None,
    detalle: bool = False,
) -> Any:
    """Consulta los fichajes / imputaciones de horas en un rango de MESES.

    Sin empleado_id devuelve un RESUMEN por empleado (número de fichajes, horas
    aprobadas, horas pendientes y fichajes rechazados/cancelados), porque el
    detalle de toda una plantilla es enorme (cientos de registros por mes).
    Con empleado_id devuelve el detalle de cada fichaje de esa persona.

    Args:
        mes_desde: mes inicial en formato YYYY-MM (p. ej. 2026-01).
        mes_hasta: mes final en formato YYYY-MM.
        empresa: nombre o _id de la sociedad (p. ej. "MiEmpresa").
        empleado_id: opcional — _id del empleado (obtenlo con buscar_empleado).
        detalle: True para forzar el detalle de toda la plantilla. Evítalo salvo
            necesidad real: un mes puede superar los 300.000 caracteres.
    """
    error = _error_rango(mes_desde, mes_hasta, "YYYY-MM", "mes")
    if error:
        return error
    if empleado_id:
        error = _error_empleado_id(empleado_id)
        if error:
            return error

    params = {"start": mes_desde, "end": mes_hasta}
    data = await _request("GET", PATHS["horas"], params=params, empresa=empresa)
    if not isinstance(data, list) or empleado_id or detalle:
        return _filtrar_por_empleado(data, empleado_id)

    nombres = await _nombres_empleados(empresa)
    if not isinstance(nombres, dict) or "error" in nombres:
        nombres = {}  # el resumen sigue siendo útil sin nombres

    resumen: dict[str, dict[str, Any]] = {}
    for f in data:
        eid = f.get("employees_id")
        r = resumen.setdefault(eid, {
            "employees_id": eid,
            "nombre": nombres.get(eid),
            "fichajes": 0,
            "horas_aprobadas": 0.0,
            "horas_pendientes": 0.0,
            "rechazados_o_cancelados": 0,
        })
        r["fichajes"] += 1
        horas = f.get("total_hours") or 0
        if f.get("status") == "done":
            r["horas_aprobadas"] += horas
        elif f.get("status") == "pending":
            r["horas_pendientes"] += horas
        else:
            r["rechazados_o_cancelados"] += 1

    for r in resumen.values():
        r["horas_aprobadas"] = round(r["horas_aprobadas"], 2)
        r["horas_pendientes"] = round(r["horas_pendientes"], 2)

    return {
        "mes_desde": mes_desde,
        "mes_hasta": mes_hasta,
        "total_fichajes": len(data),
        "empleados": len(resumen),
        "resumen": sorted(resumen.values(), key=lambda r: r["nombre"] or ""),
        "nota": "Resumen por empleado. Para ver cada fichaje, indica empleado_id.",
    }


@mcp.tool()
async def listar_ausencias(
    fecha_desde: str,
    fecha_hasta: str,
    empresa: Optional[str] = None,
    empleado_id: Optional[str] = None,
    estado: str = "vigentes",
) -> Any:
    """Lista ausencias, vacaciones y bajas en un rango de fechas.

    Args:
        fecha_desde: día inicial en formato YYYY-MM-DD.
        fecha_hasta: día final en formato YYYY-MM-DD.
        empresa: nombre o _id de la sociedad (p. ej. "MiEmpresa").
        empleado_id: opcional — _id del empleado (obtenlo con buscar_empleado).
        estado: "vigentes" (por defecto: aprobadas + pendientes), "todas",
            "aprobadas", "pendientes", "rechazadas" o "canceladas".
    """
    error = _error_rango(fecha_desde, fecha_hasta, "YYYY-MM-DD", "fecha")
    if error:
        return error
    if empleado_id:
        error = _error_empleado_id(empleado_id)
        if error:
            return error

    clave = _normalizar(estado).strip()
    if clave == "vigentes":
        estados = ESTADOS_VIGENTES
    elif clave == "todas":
        estados = None
    elif clave in ESTADOS:
        estados = {ESTADOS[clave]}
    else:
        return {
            "error": "estado_invalido",
            "detail": f"Estado '{estado}' no válido. Usa: vigentes, todas, aprobadas, pendientes, rechazadas o canceladas.",
        }

    params = {"start": fecha_desde, "end": fecha_hasta}
    data = await _request("GET", PATHS["ausencias"], params=params, empresa=empresa)
    data = _filtrar_por_empleado(data, empleado_id)
    if estados and isinstance(data, list):
        data = [a for a in data if a.get("status") in estados]
    return data


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
        empleado_id: opcional — _id del empleado (obtenlo con buscar_empleado).
    """
    error = _error_rango(mes_desde, mes_hasta, "YYYY-MM", "mes")
    if error:
        return error
    if empleado_id:
        error = _error_empleado_id(empleado_id)
        if error:
            return error

    params = {"start": mes_desde, "end": mes_hasta}
    data = await _request("GET", PATHS["turnos"], params=params, empresa=empresa)
    return _filtrar_por_empleado(data, empleado_id)


@mcp.tool()
async def saldo_vacaciones(empresa: Optional[str] = None) -> Any:
    """Consulta el saldo de vacaciones de los empleados de una sociedad.

    Cada registro trae el _id del empleado y los días pendientes a hoy
    (pending_today) y a fin de año (pending_eoy).

    Args:
        empresa: nombre o _id de la sociedad (p. ej. "MiEmpresa").
    """
    return await _request("GET", PATHS["vacaciones"], empresa=empresa)


# --------------------------------------------------------------------------- #
# Tools de escritura  [MODIFICA DATOS — confirmar siempre con el usuario]
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
        empleado_id: _id del empleado (obtenlo con buscar_empleado).
        fecha_hora: fecha y hora del fichaje en ISO 8601, p. ej. 2026-06-18T08:30:00.
        entrada: True para fichaje de entrada, False para salida.
        empresa: nombre o _id de la sociedad (p. ej. "MiEmpresa").
        tz: zona horaria del fichaje (def. Europe/Madrid).
    """
    error = _error_empleado_id(empleado_id)
    if error:
        return error
    try:
        datetime.fromisoformat(fecha_hora)
    except ValueError:
        return {"error": "fecha_invalida", "detail": f"`fecha_hora` debe ser ISO 8601 (p. ej. 2026-06-18T08:30:00); recibido '{fecha_hora}'."}

    payload = {"employees_id": empleado_id, "date": fecha_hora, "tz": tz, "in": entrada}
    return await _request("POST", PATHS["clocking"], json=payload, empresa=empresa)


# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    mcp.run()  # transporte stdio por defecto
