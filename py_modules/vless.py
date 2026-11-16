"""
Парсинг vless://-ссылок (формат Happ/v2rayTUN).

Формат ссылки:
  vless://<uuid>@<host>:<port>?<params>#<name>

Параметры (query):
  security   — reality | tls | none
  encryption — обычно none
  flow       — например xtls-rprx-vision
  sni        — serverName
  fp         — fingerprint (utls)
  pbk        — reality public key
  sid        — reality short id
  type       — tcp | ws | grpc | xhttp (по умолчанию tcp)
  path       — путь для ws/xhttp
  host       — заголовок Host для ws
  serviceName — для grpc
  headerType — тип заголовка tcp (обычно не используется с vless)
"""

from __future__ import annotations

import base64
import json
import re
import uuid as uuidlib
from typing import Any
from urllib.parse import parse_qsl, unquote, urlparse

_SUPPORTED_SCHEMES = ("vless://", "vmess://", "trojan://", "ss://")


def _b64_decode_str(data: str) -> str:
    """Декодирование base64 с терпимостью к отсутствию паддинга и пробелам."""
    cleaned = re.sub(r"\s+", "", data)
    cleaned += "=" * (-len(cleaned) % 4)
    try:
        return base64.b64decode(cleaned).decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        raise VlessParseError("Не удалось декодировать base64 содержимое подписки") from exc


def looks_like_json(content: str) -> bool:
    stripped = content.lstrip()
    return stripped.startswith("[") or stripped.startswith("{")


def _server_from_vless_outbound(name: str, outbound: dict[str, Any]) -> dict[str, Any]:
    """Нормализовать vless-outbound полного Xray-конфига в общую структуру сервера."""
    vnext_list = outbound.get("settings", {}).get("vnext", [])
    if not vnext_list:
        raise VlessParseError("В outbound отсутствует vnext")
    vnext = vnext_list[0]
    user_list = vnext.get("users", [])
    if not user_list:
        raise VlessParseError("В outbound отсутствует пользователь")
    user = user_list[0]
    try:
        user_id = str(uuidlib.UUID(user["id"]))
    except (KeyError, ValueError) as exc:
        raise VlessParseError("Невалидный uuid в outbound") from exc

    host = vnext.get("address")
    port = int(vnext.get("port", 443))
    if not host:
        raise VlessParseError("В outbound отсутствует адрес сервера")

    ss = outbound.get("streamSettings", {}) or {}
    security = (ss.get("security") or "none").lower()
    if security not in ("reality", "tls", "none"):
        raise VlessParseError(f"Неподдерживаемый тип security: {security}")
    network = (ss.get("network") or "tcp").lower()
    if network in ("h2", "http"):
        network = "xhttp"
    if network not in ("tcp", "ws", "grpc", "xhttp"):
        raise VlessParseError(f"Неподдерживаемый транспорт: {network}")

    server: dict[str, Any] = {
        "id": f"{user_id}@{host}:{port}",
        "name": name or f"{host}:{port}",
        "host": host,
        "port": port,
        "uuid": user_id,
        "security": security,
        "network": network,
        "flow": user.get("flow") or "",
        "sni": "",
        "fingerprint": "chrome",
    }

    if security == "reality":
        rs = ss.get("realitySettings", {}) or {}
        pbk = rs.get("publicKey") or rs.get("public_key") or ""
        if not pbk:
            raise VlessParseError("В конфиге с security=reality отсутствует publicKey")
        server["sni"] = rs.get("serverName") or ""
        server["fingerprint"] = rs.get("fingerprint") or "chrome"
        server["reality"] = {
            "publicKey": pbk,
            "shortId": rs.get("shortId") or "",
        }
    elif security == "tls":
        ts = ss.get("tlsSettings", {}) or {}
        server["sni"] = ts.get("serverName") or ""
        server["fingerprint"] = ts.get("fingerprint") or "chrome"
        server["allowInsecure"] = bool(ts.get("allowInsecure", False))

    if network == "ws":
        ws = ss.get("wsSettings", {}) or {}
        server["ws"] = {
            "path": ws.get("path") or "/",
            "host": (ws.get("headers") or {}).get("Host") or ws.get("host") or "",
        }
    elif network == "grpc":
        gs = ss.get("grpcSettings", {}) or {}
        server["grpc"] = {"serviceName": gs.get("serviceName") or ""}
    elif network == "xhttp":
        xs = ss.get("xhttpSettings", {}) or {}
        server["xhttp"] = {
            "path": xs.get("path") or "/",
            "host": xs.get("host") or "",
            "mode": xs.get("mode") or "",
        }
    return server


def parse_full_config_subscription(content: str) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Подписка в виде JSON: список полных конфигов Xray (v2rayN export), каждый с
    remarks и outbounds (первый vless-outbound — сервер). Также принимает одиночный
    конфиг-объект. Возвращает (серверы, ошибки) в общей нормализованной структуре.
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise VlessParseError(f"Невалидный JSON подписки: {exc}") from exc

    profiles: list[dict[str, Any]] = []
    if isinstance(data, list):
        profiles = [p for p in data if isinstance(p, dict)]
    elif isinstance(data, dict):
        profiles = [data]
    if not profiles:
        raise VlessParseError("В JSON-подписке нет конфигов")

    servers: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[str] = set()
    for profile in profiles:
        name = str(profile.get("remarks") or profile.get("tag") or "")
        outbounds = [o for o in profile.get("outbounds", []) if isinstance(o, dict)]
        vless = [o for o in outbounds if o.get("protocol") == "vless"]
        if not vless:
            errors.append(f"«{name or 'конфиг'}»: нет vless-outbound")
            continue
        try:
            server = _server_from_vless_outbound(name, vless[0])
        except VlessParseError as exc:
            errors.append(f"«{name or 'конфиг'}»: {exc}")
            continue
        if server["id"] in seen:
            continue
        seen.add(server["id"])
        servers.append(server)
    if not servers:
        raise VlessParseError("В JSON-подписке нет поддерживаемых vless-серверов")
    return servers, errors


class VlessParseError(ValueError):
    """Невалидная vless-ссылка или неподдерживаемый формат."""


def extract_links_from_subscription(content: str) -> list[str]:
    """
    Извлечь список ссылок из тела подписки Happ/v2rayTUN.

    Подписка — это base64-строка, после декодирования — список ссылок по одной на строку.
    Если содержимое не base64 — пробуем трактовать как plain-текст со ссылками.
    """
    content = content.strip()
    if not content:
        raise VlessParseError("Подписка пуста")

    decoded: str | None = None
    # Если содержимое похоже на base64 (нет «://» на первой строке) — декодируем.
    first_line = content.splitlines()[0].strip()
    if "://" not in first_line:
        decoded = _b64_decode_str(content)
    else:
        decoded = content

    links: list[str] = []
    for line in decoded.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        links.append(line)
    if not links:
        raise VlessParseError("В подписке не найдено ни одной ссылки")
    return links


def looks_like_supported_link(link: str) -> bool:
    return link.lower().startswith(_SUPPORTED_SCHEMES)


def parse_vless(link: str) -> dict[str, Any]:
    """Разобрать одну vless://-ссылку в структуру сервера."""
    if not link.lower().startswith("vless://"):
        raise VlessParseError(f"Поддерживаются только vless://-ссылки, получено: {link[:12]}...")

    raw = link[len("vless://"):]
    # Отделяем фрагмент (#name) до парсинга query.
    name = ""
    if "#" in raw:
        raw, frag = raw.split("#", 1)
        name = unquote(frag).strip()

    parsed = urlparse("vless://" + raw)
    try:
        user_id = str(uuidlib.UUID(parsed.username or ""))
    except ValueError as exc:
        raise VlessParseError("В ссылке отсутствует или невалиден uuid пользователя") from exc

    host = parsed.hostname
    if not host:
        raise VlessParseError("В ссылке отсутствует адрес сервера")
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise VlessParseError("Невалидный порт в ссылке") from exc

    params = {k.lower(): v for k, v in parse_qsl(parsed.query, keep_blank_values=True)}

    security = (params.get("security") or "none").lower()
    if security not in ("reality", "tls", "none"):
        raise VlessParseError(f"Неподдерживаемый тип security: {security}")

    network = (params.get("type") or "tcp").lower()
    if network in ("h2", "http"):
        network = "xhttp"
    if network not in ("tcp", "ws", "grpc", "xhttp"):
        raise VlessParseError(f"Неподдерживаемый транспорт: {network}")

    server: dict[str, Any] = {
        "id": f"{host}:{port}",
        "name": name or f"{host}:{port}",
        "host": host,
        "port": port,
        "uuid": user_id,
        "security": security,
        "network": network,
        "flow": params.get("flow") or "",
        "sni": params.get("sni") or "",
        "fingerprint": params.get("fp") or "chrome",
    }

    if security == "reality":
        pbk = params.get("pbk") or ""
        if not pbk:
            raise VlessParseError("В ссылке с security=reality отсутствует pbk (public key)")
        server["reality"] = {
            "publicKey": pbk,
            "shortId": params.get("sid") or "",
        }
    if security == "tls":
        server["allowInsecure"] = (params.get("allowinsecure") or "").lower() in ("1", "true")

    if network == "ws":
        server["ws"] = {
            "path": params.get("path") or "/",
            "host": params.get("host") or "",
        }
    elif network == "grpc":
        server["grpc"] = {
            "serviceName": params.get("serviceName") or params.get("servicename") or "",
        }
    elif network == "xhttp":
        server["xhttp"] = {
            "path": params.get("path") or "/",
            "host": params.get("host") or "",
        }

    return server


def parse_subscription_links(links: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """Разобрать список ссылок. Возвращает (серверы, ошибки)."""
    servers: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[str] = set()
    for link in links:
        if not looks_like_supported_link(link):
            errors.append(f"Пропущена неподдерживаемая ссылка: {link[:32]}...")
            continue
        try:
            server = parse_vless(link)
        except VlessParseError as exc:
            errors.append(str(exc))
            continue
        key = f"{server['uuid']}@{server['host']}:{server['port']}"
        if key in seen:
            continue
        seen.add(key)
        server["id"] = key
        servers.append(server)
    return servers, errors
