"""
Xray VPN Decky — VPN-клиент (Xray-core) для Steam Deck, совместимый с подписками
Happ/v2rayTUN и полными JSON-конфигами Xray.

ВАЖНО: Decky Loader перебиндивает методы класса Plugin для RPC-вызовов, из-за
чего внутренние вызовы вида self.method() ломаются (теряется binding self).
Поэтому ВСЯ логика живёт в классе Core (модульный синглтон), а Plugin содержит
только тонкие обёртки, которые не вызывают друг друга.
"""

# ── Самый первый шаг: собственный дебаг-лог ──────────────────────────────────
# Decky Loader — PyInstaller-бандл: выставляет LD_LIBRARY_PATH на свои
# распакованные либы, которые протекают в дочерние процессы (в т.ч. xray) и
# ломают их (напр. libcrypto "OPENSSL_3.4.0 not found"). Чистим СРАЗУ.
import os as _os
_os.environ.pop("LD_LIBRARY_PATH", None)

import datetime as _dt
import json as _json
import os
import sys
import traceback as _traceback

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_DEBUG_PATH = (
    _os.environ.get("DECKY_PLUGIN_SETTINGS_DIR") or _os.path.join(_PLUGIN_DIR, "settings")
) + "/debug.log"
# Дебаг-лог выключен по умолчанию (флаг "debug" в plugin.json тоже выключен).
# Для диагностики: XRAY_VPN_DECKY_DEBUG=1 в окружении plugin_loader.
_DEBUG_ENABLED = _os.environ.get("XRAY_VPN_DECKY_DEBUG", "") == "1"


def _debug(event: str, **fields) -> None:
    """Собственный JSONL-лог (только при XRAY_VPN_DECKY_DEBUG=1)."""
    if not _DEBUG_ENABLED:
        return
    try:
        _os.makedirs(_os.path.dirname(_DEBUG_PATH), exist_ok=True)
        if _os.path.exists(_DEBUG_PATH) and _os.path.getsize(_DEBUG_PATH) > 512 * 1024:
            _os.replace(_DEBUG_PATH, _DEBUG_PATH + ".1")
        payload = {"ts": _dt.datetime.now().isoformat(timespec="seconds"), "event": event}
        payload.update(fields)
        with open(_DEBUG_PATH, "a", encoding="utf-8") as _f:
            _f.write(_json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


_debug("import_start", plugin_dir=_PLUGIN_DIR, cwd=_os.getcwd())

# Decky Loader импортирует main.py без папки плагина в sys.path — добавляем сами.
sys.path.insert(0, _PLUGIN_DIR)

try:
    import decky  # предоставляется Decky Loader в рантайме
    logger = decky.logger
except ImportError:  # локальная отладка вне Steam Deck
    import logging

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("xray-vpn-decky")

# Уровень логирования управляется флагом "debug" в plugin.json (сейчас выключен).

try:
    from py_modules.pingutil import ping_servers
    from py_modules.subscriptions import SubscriptionError, SubscriptionManager
    from py_modules.xray import XrayError, XrayManager
except Exception:  # noqa: BLE001
    _debug("import_failed", traceback=_traceback.format_exc())
    raise

_debug("import_ok")

import asyncio
import functools
import json
import logging
import time
from pathlib import Path
from typing import Any

_debug("module_ready")

# Декоратор: логирует каждый вызов метода Core с таймингом и traceback при ошибке.
def _debug_call(fn):
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        qual = fn.__qualname__
        safe_args = [_sanitize(a) for a in args[1:]]  # args[0] = self
        safe_kwargs = {k: _sanitize(v) for k, v in kwargs.items()}
        logger.debug(f"[xray-vpn-decky] CALL {qual} args={safe_args} kwargs={safe_kwargs}")
        t0 = time.perf_counter()
        try:
            result = await fn(*args, **kwargs)
            elapsed = time.perf_counter() - t0
            result_note = ""
            if isinstance(result, dict) and "server_count" in result:
                result_note = f" servers={result['server_count']}"
            elif isinstance(result, dict) and "state" in result:
                result_note = f" state={result['state']}"
            logger.debug(f"[xray-vpn-decky] OK   {qual} in {elapsed:.2f}s{result_note}")
            return result
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(f"[xray-vpn-decky] FAIL {qual}")
            raise

    return wrapper


def _sanitize(value: Any) -> str:
    """Секреты (ссылки, base64, uuid) не попадают в лог — только длина/тип."""
    if isinstance(value, str):
        if len(value) > 48 or value.startswith(("vless://", "vmess://", "https://", "http://")):
            if value.startswith("http"):
                # оставляем только хост
                try:
                    from urllib.parse import urlparse

                    return f"<url {urlparse(value).netloc}>"
                except Exception:  # noqa: BLE001
                    return f"<url len={len(value)}>"
            return f"<str len={len(value)}>"
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return repr(value)
    return f"<{type(value).__name__}>"

CALL_TIMEOUT = 35

DEFAULT_SETTINGS: dict[str, Any] = {
    "auto_connect_on_start": False,
    "auto_connect_min_ping": False,
    "reconnect_on_switch": True,
}


def _env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default))


class Core:
    """Вся логика плагина. Экземпляр не трогает Decky Loader — внутренние
    вызовы методов здесь безопасны."""

    def __init__(self) -> None:
        plugin_dir = _env_path("DECKY_PLUGIN_DIR", str(Path(__file__).resolve().parent))
        settings_dir = _env_path("DECKY_PLUGIN_SETTINGS_DIR", str(Path(plugin_dir) / "settings"))
        self._plugin_dir = Path(plugin_dir)
        self._settings_dir = Path(settings_dir)
        self._settings_dir.mkdir(parents=True, exist_ok=True)
        self._settings_path = self._settings_dir / "plugin_settings.json"

        self._xray = XrayManager(self._plugin_dir, self._settings_dir, logger)
        self._subs = SubscriptionManager(self._settings_dir, logger)
        self._settings = self._load_settings()
        self._watchdog_task: asyncio.Task | None = None
        self._last_watchdog_attempt = 0.0
        _debug("core_init", settings_dir=str(self._settings_dir), xray_ready=self._xray.state()["xray_ready"])

    # ---------------- lifecycle ----------------

    async def start(self) -> None:
        """Точка входа, вызывается из Plugin._main."""
        _debug("core_start", auto_connect=bool(self._settings.get("auto_connect_on_start")))
        self._watchdog_task = asyncio.create_task(self._watchdog())
        await asyncio.sleep(8)
        if self._settings.get("auto_connect_on_start"):
            await self._maybe_auto_connect()

    async def stop(self) -> None:
        """Вызывается при выгрузке плагина / перезапуске Steam."""
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
        await self._xray.stop_all()

    async def _watchdog(self) -> None:
        """Восстановление сессии после сна/обрыва при включённом автоподключении."""
        while True:
            await asyncio.sleep(15)
            try:
                state = self._xray.state()["state"]
                if (
                    self._settings.get("auto_connect_on_start")
                    and state == "disconnected"
                    and self._subs.selected_server() is not None
                    and time.time() - self._last_watchdog_attempt > 60
                ):
                    self._last_watchdog_attempt = time.time()
                    logger.info("[xray-vpn-decky] watchdog: restoring connection")
                    await self._connect_selected()
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                logger.error(f"[xray-vpn-decky] watchdog error: {exc}")

    # ---------------- settings ----------------

    def _load_settings(self) -> dict[str, Any]:
        try:
            if self._settings_path.exists():
                data = json.loads(self._settings_path.read_text(encoding="utf-8"))
                return {**DEFAULT_SETTINGS, **data}
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[xray-vpn-decky] failed to load settings: {exc}")
        return dict(DEFAULT_SETTINGS)

    def _save_settings(self) -> None:
        tmp = self._settings_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._settings, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._settings_path)

    async def get_settings(self) -> dict[str, Any]:
        return dict(self._settings)

    async def set_setting(self, key: str, value: Any) -> dict[str, Any]:
        if key not in DEFAULT_SETTINGS:
            raise SubscriptionError(f"Неизвестная настройка: {key}")
        self._settings[key] = value
        self._save_settings()
        return dict(self._settings)

    # ---------------- helpers ----------------

    async def _guarded(self, description: str, coro):
        """Выполнить бэкенд-операцию с жёстким таймаутом и понятной ошибкой."""
        try:
            return await asyncio.wait_for(coro, timeout=CALL_TIMEOUT)
        except asyncio.TimeoutError as exc:
            logger.error(f"[xray-vpn-decky] {description}: timeout after {CALL_TIMEOUT}s")
            raise SubscriptionError(
                f"«{description}»: превышено время ожидания ({CALL_TIMEOUT} с). "
                "Проверьте сеть Deck и логи: Настройки → Показать логи."
            ) from exc

    # ---------------- state ----------------

    async def get_state(self) -> dict[str, Any]:
        servers = []
        for s in self._subs.active_servers():
            servers.append(
                {
                    "id": s["id"],
                    "name": s["name"],
                    "host": s["host"],
                    "port": s["port"],
                    "ping": s.get("ping"),
                }
            )
        sel = self._subs.selected_server()
        return {
            **self._xray.state(),
            "subscriptions": self._subs.list(),
            "active_subscription_id": self._subs.active_id(),
            "servers": servers,
            "selected_server_id": sel["id"] if sel else None,
            "has_subscriptions": len(self._subs.list()) > 0,
        }

    # ---------------- subscriptions ----------------

    async def add_subscription(self, url_or_b64: str, name: str = "") -> dict[str, Any]:
        logger.info(f"[xray-vpn-decky] add_subscription: start (len={len(url_or_b64)})")
        result = await self._guarded("Добавление подписки", self._subs.add(url_or_b64, name or None))
        logger.info("[xray-vpn-decky] add_subscription: done")
        return self._public_sub(result)

    async def update_subscription(self, sub_id: str) -> dict[str, Any]:
        logger.info(f"[xray-vpn-decky] update_subscription: {sub_id}")
        return self._public_sub(await self._guarded("Обновление подписки", self._subs.update(sub_id)))

    async def rename_subscription(self, sub_id: str, name: str) -> None:
        self._subs.rename(sub_id, name)

    async def remove_subscription(self, sub_id: str) -> dict[str, Any]:
        """Удаление подписки; если она была активна и идёт подключение — останавливаем VPN."""
        was_active = self._subs.remove(sub_id)
        if was_active and self._xray.state()["state"] in ("connected", "connecting"):
            await self._xray.disconnect()
        return await self.get_state()

    async def set_active_subscription(self, sub_id: str) -> dict[str, Any]:
        self._subs.set_active(sub_id)
        return await self.get_state()

    @staticmethod
    def _public_sub(sub: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": sub["id"],
            "name": sub["name"],
            "server_count": len(sub.get("servers", [])),
            "updated_at": sub.get("updated_at", 0),
        }

    # ---------------- servers ----------------

    async def select_server(self, server_id: str) -> dict[str, Any]:
        self._subs.select_server(server_id)
        state = self._xray.state()["state"]
        if self._settings.get("reconnect_on_switch") and state in ("connected", "connecting"):
            await self._connect_selected()
        return await self.get_state()

    async def ping_all(self) -> dict[str, Any]:
        servers = self._subs.active_servers()
        if not servers:
            raise SubscriptionError("Нет серверов для проверки")
        results = await ping_servers(servers)
        self._subs.update_pings(results)
        return results

    # ---------------- connection ----------------

    async def _maybe_auto_connect(self) -> None:
        if self._settings.get("auto_connect_min_ping"):
            try:
                servers = self._subs.active_servers()
                results = await ping_servers(servers)
                self._subs.update_pings(results)
                best = min(
                    (s for s in servers if results.get(s["id"]) is not None),
                    key=lambda s: results[s["id"]],
                    default=None,
                )
                if best:
                    self._subs.select_server(best["id"])
            except Exception as exc:  # noqa: BLE001
                logger.error(f"[xray-vpn-decky] auto min-ping failed: {exc}")
        await self._connect_selected()

    async def _connect_selected(self) -> None:
        server = self._subs.selected_server()
        if not server:
            raise SubscriptionError("Не выбран сервер")
        await self._do_connect()

    async def _do_connect(self) -> None:
        server = self._subs.selected_server()
        if not server:
            raise SubscriptionError("Добавьте подписку и выберите сервер")
        try:
            await self._xray.connect(server)
        except XrayError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise XrayError(f"Ошибка подключения: {exc}") from exc

    async def connect(self) -> dict[str, Any]:
        await self._guarded("Подключение", self._do_connect())
        return await self.get_state()

    async def disconnect(self) -> dict[str, Any]:
        await self._xray.disconnect()
        return await self.get_state()

    async def ensure_xray(self) -> dict[str, Any]:
        """Проверить/скачать ядро Xray (из настроек). Возвращает версию."""
        await self._guarded("Загрузка ядра Xray", self._xray.ensure_binary())
        return {"xray_ready": True, "xray_version": self._xray.state().get("xray_version")}

    # ---------------- logs ----------------

    async def get_logs(self, max_lines: int = 200) -> str:
        log_dir = _env_path("DECKY_PLUGIN_LOG_DIR", str(self._settings_dir))
        lines: list[str] = []
        for name in ("debug.log", "plugin.log", "xray.log", "xray_access.log"):
            path: Path | None = None
            for base in (Path(log_dir), self._settings_dir):
                candidate = base / name
                if candidate.exists():
                    path = candidate
                    break
            if path is None:
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
                lines.append(f"===== {name} =====")
                lines.extend(content.splitlines()[-max_lines:])
            except Exception as exc:  # noqa: BLE001
                lines.append(f"({name}: {exc})")
        return "\n".join(lines[-max_lines * 2:])


_core: Core | None = None

# Прозрачно оборачиваем публичные методы Core в дебаг-логирование.
for _m in (
    "start", "stop", "get_settings", "set_setting", "get_state",
    "add_subscription", "update_subscription", "rename_subscription",
    "remove_subscription", "set_active_subscription", "select_server",
    "ping_all", "connect", "disconnect", "ensure_xray", "get_logs",
):
    setattr(Core, _m, _debug_call(getattr(Core, _m)))
del _m


def _get_core() -> Core:
    global _core
    if _core is None:
        _core = Core()
    return _core


class Plugin:
    """Тонкие обёртки над Core. Методы не вызывают друг друга — Decky Loader
    перебиндивает их для RPC, и внутренние self-вызовы ломаются."""

    # --- lifecycle (вызываются загрузчиком) ---

    async def _main(self) -> None:
        await _get_core().start()

    def _unload(self) -> None:
        try:
            asyncio.get_event_loop().create_task(_get_core().stop())
        except RuntimeError:
            pass

    # --- RPC-методы (вызываются фронтендом) ---

    async def get_settings(self) -> dict[str, Any]:
        return await _get_core().get_settings()

    async def set_setting(self, key: str, value: Any) -> dict[str, Any]:
        return await _get_core().set_setting(key, value)

    async def get_state(self) -> dict[str, Any]:
        return await _get_core().get_state()

    async def add_subscription(self, url_or_b64: str, name: str = "") -> dict[str, Any]:
        return await _get_core().add_subscription(url_or_b64, name)

    async def update_subscription(self, sub_id: str) -> dict[str, Any]:
        return await _get_core().update_subscription(sub_id)

    async def rename_subscription(self, sub_id: str, name: str) -> None:
        return await _get_core().rename_subscription(sub_id, name)

    async def remove_subscription(self, sub_id: str) -> dict[str, Any]:
        return await _get_core().remove_subscription(sub_id)

    async def set_active_subscription(self, sub_id: str) -> dict[str, Any]:
        return await _get_core().set_active_subscription(sub_id)

    async def select_server(self, server_id: str) -> dict[str, Any]:
        return await _get_core().select_server(server_id)

    async def ping_all(self) -> dict[str, Any]:
        return await _get_core().ping_all()

    async def connect(self) -> dict[str, Any]:
        return await _get_core().connect()

    async def disconnect(self) -> dict[str, Any]:
        return await _get_core().disconnect()

    async def ensure_xray(self) -> dict[str, Any]:
        return await _get_core().ensure_xray()

    async def get_logs(self, max_lines: int = 200) -> str:
        return await _get_core().get_logs(max_lines)
