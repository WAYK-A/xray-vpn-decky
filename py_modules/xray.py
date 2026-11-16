"""
Менеджер ядра Xray-core: скачивание, конфигурация (TUN), процесс, мониторинг.

- Бинарник хранится в <plugin_dir>/bin/xray.
- Конфиги — в <plugin_dir>/settings/xray_config_<id>.json.
- Запуск — отдельный процесс `xray run -c config.json`.
- При обрыве процесса — до 3 попыток переподключения с экспоненциальной задержкой.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import aiohttp

TUN_NAME = "xray0"

# Sudoers-строка для одноразовой выдачи прав (инструкция показывается в ошибке).
SUDOERS_LINE = "deck ALL=(root) NOPASSWD: {bin_path}"


def _clean_env() -> dict[str, str]:
    """Окружение для дочерних процессов без LD_LIBRARY_PATH из PyInstaller-бандла
    Decky Loader — иначе xray/ip/systemctl подхватывают чужие либы и падают."""
    env = dict(os.environ)
    env.pop("LD_LIBRARY_PATH", None)
    return env


def _ip_cmd() -> str | None:
    """Путь к iproute2 (в PATH бэкенда его может не быть)."""
    return shutil.which("ip") or next(
        (p for p in ("/usr/sbin/ip", "/sbin/ip", "/usr/bin/ip") if os.path.exists(p)), None
    )


def _sudo_probe(bin_path: str | None = None) -> tuple[bool, str]:
    """Пробник passwordless sudo: (ok, детали для диагностики).
    ВАЖНО: sudoers-дропин разрешает только конкретный бинарник xray,
    поэтому проверяем `sudo -n <bin> version`, а не `sudo -n true`."""
    sudo = shutil.which("sudo") or ("/usr/bin/sudo" if os.path.exists("/usr/bin/sudo") else None)
    if not sudo:
        return False, "sudo не найден (ни в PATH, ни /usr/bin)"
    probe = [bin_path, "version"] if bin_path else ["true"]
    try:
        r = subprocess.run(
            [sudo, "-n"] + probe, capture_output=True, text=True, timeout=60, env=_clean_env(),
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"пробник упал с исключением: {exc}"
    if r.returncode == 0:
        return True, "ok"
    err = (r.stderr or r.stdout or "").strip()[:300]
    return False, f"rc={r.returncode}: {err or 'нет вывода'}"


def _sudoers_dropin_status(bin_path: str) -> str:
    """Диагностика /etc/sudoers.d/xray-vpn-decky (файл читается всеми)."""
    import getpass

    user = getpass.getuser()
    p = Path("/etc/sudoers.d/xray-vpn-decky")
    if not p.exists():
        return f"пользователь={user}; файл /etc/sudoers.d/xray-vpn-decky ОТСУТСТВУЕТ"
    try:
        content = p.read_text(errors="replace")
    except Exception as exc:  # noqa: BLE001
        return f"пользователь={user}; файл есть, прочитать не удалось: {exc}"
    if bin_path in content:
        return f"пользователь={user}; дропин на месте и содержит путь к бинарнику"
    return f"пользователь={user}; файл есть, но путь {bin_path} в нём НЕ найден: {content.strip()[:200]}"


def _has_net_admin_caps(bin_path: str) -> bool:
    """У бинарника выставлены file capabilities (setcap)? Тогда xray может
    создать TUN-интерфейс сам, без sudo."""
    for cap_tool in (shutil.which("getcap"), "/usr/sbin/getcap", "/sbin/getcap"):
        if not cap_tool or not os.path.exists(cap_tool):
            continue
        try:
            r = subprocess.run([cap_tool, bin_path], capture_output=True, text=True, timeout=10)
            return "cap_net_admin" in (r.stdout or "")
        except Exception:  # noqa: BLE001
            continue
    return False

XRAY_RELEASES_API = "https://api.github.com/repos/XTLS/Xray-core/releases/latest"
XRAY_DOWNLOAD = "https://github.com/XTLS/Xray-core/releases/download/{tag}/Xray-linux-64.zip"
XRAY_DGST = "https://github.com/XTLS/Xray-core/releases/download/{tag}/Xray-linux-64.zip.dgst"

# Запасные зеркала GitHub Releases (используются, если прямое скачивание не отвечает).
DOWNLOAD_MIRRORS = [
    "",  # прямое скачивание с GitHub
    "https://gh-proxy.com/",
    "https://ghproxy.net/",
]

DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=300, connect=15, sock_read=30)

# Состояния подключения
STATE_DISCONNECTED = "disconnected"
STATE_CONNECTING = "connecting"
STATE_CONNECTED = "connected"
STATE_ERROR = "error"

MAX_RETRIES = 3
RETRY_DELAYS = (2, 4, 8)


class XrayError(Exception):
    pass


def build_xray_config(server: dict[str, Any]) -> dict[str, Any]:
    """Собрать конфиг Xray из выбранного сервера: TUN-вход + vless-исход."""
    stream_settings: dict[str, Any] = {"network": server.get("network", "tcp")}

    security = server.get("security", "none")
    if security == "reality":
        reality = server.get("reality", {})
        stream_settings["security"] = "reality"
        stream_settings["realitySettings"] = {
            "show": False,
            "fingerprint": server.get("fingerprint") or "chrome",
            "serverName": server.get("sni") or server["host"],
            "publicKey": reality.get("publicKey", ""),
            "shortId": reality.get("shortId", ""),
        }
    elif security == "tls":
        stream_settings["security"] = "tls"
        stream_settings["tlsSettings"] = {
            "serverName": server.get("sni") or server["host"],
            "fingerprint": server.get("fingerprint") or "chrome",
            "allowInsecure": bool(server.get("allowInsecure", False)),
        }

    network = server.get("network", "tcp")
    if network == "ws":
        ws = server.get("ws", {})
        stream_settings["wsSettings"] = {
            "path": ws.get("path", "/"),
            "headers": ({ "Host": ws["host"] } if ws.get("host") else {}),
        }
    elif network == "grpc":
        grpc = server.get("grpc", {})
        stream_settings["grpcSettings"] = {
            "serviceName": grpc.get("serviceName", ""),
            "multiMode": False,
        }
    elif network == "xhttp":
        xhttp = server.get("xhttp", {})
        xhttp_settings: dict[str, Any] = {
            "path": xhttp.get("path", "/"),
            "host": xhttp.get("host", ""),
        }
        if xhttp.get("mode"):
            xhttp_settings["mode"] = xhttp["mode"]
        stream_settings["xhttpSettings"] = xhttp_settings

    vless_user: dict[str, Any] = {
        "id": server["uuid"],
        "encryption": "none",
    }
    if server.get("flow"):
        vless_user["flow"] = server["flow"]

    config: dict[str, Any] = {
        "log": {"loglevel": "debug", "access": "", "error": ""},
        "dns": {
            "servers": [
                {"address": "https://dns.google/dns-query", "domains": ["geosite:geolocation-!cn"]},
                {"address": "https://cloudflare-dns.com/dns-query", "domains": ["geosite:geolocation-!cn"]},
                {"address": "local", "domains": ["geosite:cn"], "skipFallback": True},
                "localhost",
            ],
            "queryStrategy": "UseIPv4",
        },
        "inbounds": [
            {
                "tag": "tun-in",
                "protocol": "tun",
                "settings": {
                    "name": TUN_NAME,
                    "mtu": 1500,
                    "gateway": ["172.19.0.1/30"],
                    "autoSystemRoutingTable": ["0.0.0.0/0", "::/0"],
                    "autoOutboundsInterface": "auto",
                    # DNS-захват делаем правилом маршрутизации (port 53 -> dns-out),
                    # а не подменой системного резолвера: autoSystemDnsToGateway=true
                    # требует слушающий DNS на шлюзе, иначе xray падает при старте.
                    "autoSystemDnsToGateway": False,
                },
            }
        ],
        "outbounds": [
            {
                "tag": "proxy",
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": server["host"],
                            "port": int(server["port"]),
                            "users": [vless_user],
                        }
                    ]
                },
                "streamSettings": stream_settings,
            },
            {"tag": "dns-out", "protocol": "dns"},
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "block", "protocol": "blackhole"},
        ],
        "routing": {
            "domainStrategy": "IPIfNonMatch",
            "rules": [
                {"type": "field", "inboundTag": ["tun-in"], "port": 53, "outboundTag": "dns-out"},
                {"type": "field", "ip": ["geoip:private"], "outboundTag": "direct"},
                {"type": "field", "domain": ["geosite:private"], "outboundTag": "direct"},
            ],
        },
    }
    return config


class XrayManager:
    def __init__(self, plugin_dir: Path, settings_dir: Path, logger, state_cb=None) -> None:
        self._plugin_dir = plugin_dir
        self._settings_dir = settings_dir
        self._logger = logger
        self._state_cb = state_cb
        self._proc: asyncio.subprocess.Process | None = None
        self._monitor_task: asyncio.Task | None = None
        self._retry_task: asyncio.Task | None = None
        self._state = STATE_DISCONNECTED
        self._error: str | None = None
        self._server: dict[str, Any] | None = None
        self._session_started: float = 0.0
        self._manual_disconnect = False
        self._last_exit_rc: int | None = None
        self._bin_path = plugin_dir / "bin" / "xray"

    # ---------- state ----------

    def _set_state(self, state: str, error: str | None = None) -> None:
        if state != self._state or error != self._error:
            self._logger.info(
                f"[xray-vpn-decky] state: {self._state} -> {state}"
                + (f" ({error})" if error else "")
            )
        self._state = state
        self._error = error
        if self._state_cb:
            try:
                self._state_cb()
            except Exception as exc:  # noqa: BLE001
                self._logger.error(f"[xray-vpn-decky] state callback failed: {exc}")

    def state(self) -> dict[str, Any]:
        return {
            "state": self._state,
            "error": self._error,
            "server": (
                {k: v for k, v in self._server.items() if k in ("id", "name", "host", "port", "ping")}
                if self._server else None
            ),
            "session_seconds": (
                int(asyncio.get_event_loop().time() - self._session_started)
                if self._state == STATE_CONNECTED and self._session_started else 0
            ),
            "xray_ready": self._bin_path.exists(),
            "xray_version": self._xray_version(),
        }

    def _xray_version(self) -> str | None:
        ver_file = self._plugin_dir / "bin" / "version.txt"
        try:
            return ver_file.read_text(encoding="utf-8").strip() or None
        except Exception:  # noqa: BLE001
            return None

    # ---------- binary ----------

    async def _latest_tag(self) -> str:
        """Определить тег последнего релиза: GitHub API, при недоступности — редирект /releases/latest."""
        headers = {"User-Agent": "xray-vpn-decky-plugin"}
        api_timeout = aiohttp.ClientTimeout(total=20, connect=10)
        try:
            async with aiohttp.ClientSession(timeout=api_timeout, headers=headers) as session:
                async with session.get(XRAY_RELEASES_API) as resp:
                    if resp.status == 200:
                        release = await resp.json()
                        tag = release.get("tag_name")
                        if tag:
                            return str(tag)
                    self._logger.warning(f"[xray-vpn-decky] github api status {resp.status}, trying redirect")
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(f"[xray-vpn-decky] github api failed: {exc}")

        # Fallback: страница релизов редиректит на /releases/tag/<tag>.
        try:
            async with aiohttp.ClientSession(timeout=api_timeout, headers=headers) as session:
                async with session.get(
                    "https://github.com/XTLS/Xray-core/releases/latest", allow_redirects=False
                ) as resp:
                    location = resp.headers.get("Location", "")
                    tag = location.rstrip("/").rsplit("/", 1)[-1]
                    if resp.status in (301, 302, 303, 307, 308) and tag.startswith("v"):
                        return tag
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(f"[xray-vpn-decky] github redirect failed: {exc}")
        raise XrayError("Не удалось определить версию Xray-core (нет доступа к GitHub)")

    async def ensure_binary(self) -> None:
        """Скачать Xray-linux-64.zip при отсутствии бинарника, проверить SHA256 по .dgst."""
        if self._bin_path.exists():
            self._logger.debug(f"[xray-vpn-decky] xray binary present at {self._bin_path}")
            return
        bin_dir = self._plugin_dir / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        self._logger.info("[xray-vpn-decky] downloading xray-core...")

        tag = await self._latest_tag()

        zip_path = bin_dir / "xray.zip"
        errors: list[str] = []
        downloaded = False
        for mirror in DOWNLOAD_MIRRORS:
            try:
                await self._download(mirror + XRAY_DOWNLOAD.format(tag=tag), zip_path)
                downloaded = True
                break
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{mirror or 'github'}: {exc}")
                self._logger.warning(f"[xray-vpn-decky] xray download via {mirror or 'github'} failed: {exc}")
        if not downloaded:
            zip_path.unlink(missing_ok=True)
            raise XrayError("Не удалось скачать Xray: " + " | ".join(errors))

        # Проверка целостности: dgst с того же зеркала, что и архив (если доступно).
        expected: str | None = None
        for mirror in DOWNLOAD_MIRRORS:
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30, connect=10)) as session:
                    async with session.get(mirror + XRAY_DGST.format(tag=tag)) as resp:
                        if resp.status == 200:
                            expected = self._parse_dgst((await resp.text()).strip())
                            if expected:
                                break
            except Exception:  # noqa: BLE001
                continue
        if expected:
            actual = hashlib.sha256(zip_path.read_bytes()).hexdigest()
            if actual.lower() != expected.lower():
                zip_path.unlink(missing_ok=True)
                raise XrayError("Проверка целостности Xray не пройдена (SHA256)")

        try:
            with zipfile.ZipFile(zip_path) as zf:
                with zf.open("xray") as src, open(self._bin_path, "wb") as dst:
                    dst.write(src.read())
            zip_path.unlink(missing_ok=True)
            self._bin_path.chmod(self._bin_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            (bin_dir / "version.txt").write_text(tag, encoding="utf-8")
            self._logger.info(f"[xray-vpn-decky] xray-core {tag} installed")
        except Exception as exc:  # noqa: BLE001
            self._bin_path.unlink(missing_ok=True)
            raise XrayError(f"Не удалось распаковать Xray: {exc}") from exc

    async def _download(self, url: str, dest: Path) -> None:
        async with aiohttp.ClientSession(timeout=DOWNLOAD_TIMEOUT) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise XrayError(f"HTTP {resp.status}")
                with open(dest, "wb") as fh:
                    while True:
                        chunk = await resp.content.read(1 << 20)
                        if not chunk:
                            break
                        fh.write(chunk)

    @staticmethod
    def _parse_dgst(text: str) -> str | None:
        for line in text.splitlines():
            parts = line.split()
            for part in parts:
                if len(part) == 64 and all(c in "0123456789abcdefABCDEF" for c in part):
                    return part
        return None

    # ---------- process ----------

    def _config_path(self, server_id: str) -> Path:
        safe = hashlib.sha1(server_id.encode()).hexdigest()[:12]
        return self._settings_dir / f"xray_config_{safe}.json"

    def _ensure_tun(self) -> None:
        """modprobe tun + /dev/net/tun при отсутствии. Бэкенд обычно НЕ root —
        пробуем обычный вызов и sudo -n (для mknod под root через sh -c)."""
        try:
            if not os.path.exists("/dev/net/tun"):
                subprocess.run(["modprobe", "tun"], capture_output=True, env=_clean_env())
            if not os.path.exists("/dev/net/tun") and os.geteuid() == 0:
                os.makedirs("/dev/net", exist_ok=True)
                os.mknod("/dev/net/tun", 0o666 | stat.S_IFCHR, os.makedev(10, 200))
                os.chmod("/dev/net/tun", 0o666)
                self._logger.info("[xray-vpn-decky] /dev/net/tun created")
            if not os.path.exists("/dev/net/tun") and _sudo_probe()[0]:
                subprocess.run(
                    ["sudo", "-n", "sh", "-c",
                     "mkdir -p /dev/net && mknod /dev/net/tun c 10 200 && chmod 666 /dev/net/tun"],
                    capture_output=True, env=_clean_env(),
                )
                if os.path.exists("/dev/net/tun"):
                    self._logger.info("[xray-vpn-decky] /dev/net/tun created via sudo")
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(f"[xray-vpn-decky] ensure_tun: {exc}")

    def _apply_caps(self) -> None:
        """setcap на бинарник — нужно, если бэкенд вдруг не root."""
        try:
            r = subprocess.run(["getcap", str(self._bin_path)], capture_output=True, text=True, env=_clean_env())
            if "cap_net_admin" not in r.stdout and os.geteuid() == 0:
                subprocess.run(
                    ["setcap", "cap_net_admin,cap_net_bind_service,cap_net_raw=+eip", str(self._bin_path)],
                    capture_output=True, env=_clean_env(),
                )
                self._logger.info("[xray-vpn-decky] capabilities applied to xray binary")
        except FileNotFoundError:
            pass  # setcap/getcap отсутствуют — ок, если бэкенд root
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(f"[xray-vpn-decky] apply_caps: {exc}")

    async def _wait_tun_up(self, timeout: float = 20.0) -> bool | None:
        """Дождаться адреса на TUN-интерфейсе. None = не удалось проверить (нет ip)."""
        ip = _ip_cmd()
        if not ip:
            return None
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            try:
                r = await loop.run_in_executor(
                    None,
                    lambda: subprocess.run(
                        [ip, "-j", "addr", "show", TUN_NAME],
                        capture_output=True, text=True, timeout=5, env=_clean_env(),
                    ),
                )
                if r.returncode == 0:
                    data = json.loads(r.stdout)
                    if data and data[0].get("addr_info"):
                        return True
            except FileNotFoundError:
                return None
            except Exception as exc:  # noqa: BLE001
                self._logger.debug(f"[xray-vpn-decky] wait_tun_up check failed: {exc}")
                return None
            await asyncio.sleep(0.5)
        return False

    def _disable_tun_ipv6(self) -> None:
        """Happy Eyeballs: без этого приложения ждут TCP-таймаут на AAAA (~20 с)."""
        sysctl = ["sysctl", "-w", f"net.ipv6.conf.{TUN_NAME}.disable_ipv6=1"]
        if os.geteuid() != 0 and _sudo_probe()[0]:
            sysctl = ["sudo", "-n"] + sysctl
        try:
            subprocess.run(sysctl, capture_output=True, env=_clean_env())
            self._logger.info(f"[xray-vpn-decky] IPv6 disabled on {TUN_NAME}")
        except Exception as exc:  # noqa: BLE001
            self._logger.debug(f"[xray-vpn-decky] disable_tun_ipv6: {exc}")

    def _rotate_logs(self) -> None:
        """Не даём логам xray разрастись: оставляем последние ~1 МБ."""
        for name in ("xray.log", "xray_access.log"):
            path = self._settings_dir / name
            try:
                if path.exists() and path.stat().st_size > 5 * 1024 * 1024:
                    data = path.read_bytes()[-1024 * 1024:]
                    path.write_bytes(data)
                    self._logger.info(f"[xray-vpn-decky] rotated {name}")
            except Exception as exc:  # noqa: BLE001
                self._logger.debug(f"[xray-vpn-decky] rotate {name}: {exc}")

    async def connect(self, server: dict[str, Any]) -> None:
        await self.disconnect(silent=True)
        await self.ensure_binary()
        self._apply_caps()
        self._ensure_tun()
        self._rotate_logs()

        config = build_xray_config(server)
        # Логи ядра — в файлы (settings/xray.log, xray_access.log): пустые строки
        # в конфиге означают «не писать», поэтому выставляем пути явно.
        config["log"] = {
            "loglevel": "debug",
            "access": str(self._settings_dir / "xray_access.log"),
            "error": str(self._settings_dir / "xray.log"),
        }
        self._settings_dir.mkdir(parents=True, exist_ok=True)
        config_path = self._config_path(str(server["id"]))
        # Секреты (uuid, pbk) хранятся только в этом файле и не попадают в логи.
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        self._logger.debug(f"[xray-vpn-decky] config written: {config_path.name}")

        self._manual_disconnect = False
        self._server = server
        self._set_state(STATE_CONNECTING)
        await self._start_process(config_path)

    def _xray_command(self, config_path: Path) -> list[str]:
        """Команда запуска xray. Приоритет: root → file capabilities (setcap) →
        sudo -n. Бэкенд Decky обычно НЕ root (euid=deck), TUN-входу нужен
        CAP_NET_ADMIN."""
        base = [str(self._bin_path), "run", "-c", str(config_path)]
        if os.geteuid() == 0:
            return base
        if _has_net_admin_caps(str(self._bin_path)):
            self._logger.info("[xray-vpn-decky] using file capabilities (setcap), no sudo needed")
            return base
        ok, detail = _sudo_probe(str(self._bin_path))
        if ok:
            return ["sudo", "-n"] + base
        dropin = _sudoers_dropin_status(str(self._bin_path))
        self._logger.error(f"[xray-vpn-decky] no way to elevate xray: sudo={detail}; {dropin}")
        raise XrayError(
            "Нет прав для создания VPN-интерфейса (CAP_NET_ADMIN). "
            f"sudo: {detail}. "
            "Выполните ОДНО из двух в Konsole (Desktop Mode) и подключитесь снова: "
            f"(1) рекомендуется: sudo setcap 'cap_net_admin,cap_net_bind_service,cap_net_raw=+eip' {self._bin_path} ; "
            f'(2) echo "{SUDOERS_LINE.format(bin_path=self._bin_path)}" | sudo tee /etc/sudoers.d/xray-vpn-decky'
        )

    async def _start_process(self, config_path: Path) -> None:
        log_path = self._settings_dir / "xray.log"
        try:
            cmd = self._xray_command(config_path)
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=open(log_path, "ab"),
                stderr=asyncio.subprocess.STDOUT,
                env=_clean_env(),
            )
            self._logger.info(f"[xray-vpn-decky] xray started (via sudo={cmd[0] == 'sudo'}), pid={self._proc.pid}")
        except Exception as exc:  # noqa: BLE001
            self._set_state(STATE_ERROR, f"Не удалось запустить Xray: {exc}")
            raise XrayError(str(exc)) from exc

        self._monitor_task = asyncio.create_task(self._monitor())
        # Проверяем, что процесс жив и TUN-интерфейс поднялся с адресом.
        await asyncio.sleep(1)
        proc = self._proc
        if proc is None or proc.returncode is not None:
            # Процесс уже завершился — монитор мог обнулить self._proc.
            await self.disconnect(silent=True)
            rc = self._last_exit_rc
            self._set_state(STATE_ERROR, f"Xray завершился сразу после запуска (код {rc})")
            raise XrayError(
                f"Xray не запустился (код {rc}). Откройте Настройки → Показать логи: xray.log"
            )
        tun = await self._wait_tun_up()
        if tun is False:
            self._logger.error(f"[xray-vpn-decky] {TUN_NAME} did not come up, see xray.log")
            await self.disconnect(silent=True)
            self._set_state(STATE_ERROR, f"{TUN_NAME} не поднялся — см. xray.log")
            raise XrayError(f"Интерфейс {TUN_NAME} не поднялся. См. xray.log")
        self._disable_tun_ipv6()
        self._session_started = asyncio.get_event_loop().time()
        self._set_state(STATE_CONNECTED)

    async def _monitor(self) -> None:
        assert self._proc is not None
        rc = await self._proc.wait()
        self._last_exit_rc = rc
        self._proc = None
        self._logger.info(f"[xray-vpn-decky] xray exited, rc={rc}, state={self._state}")
        if self._state == STATE_CONNECTED:
            self._logger.warning(f"[xray-vpn-decky] xray exited unexpectedly, rc={rc}")
            self._set_state(STATE_ERROR, "Соединение прервано")
            if not self._manual_disconnect and self._server is not None:
                self._retry_task = asyncio.create_task(self._retry_loop())
        elif self._state == STATE_CONNECTING:
            self._set_state(STATE_ERROR, f"Xray не запустился (код {rc}). См. логи")

    async def _retry_loop(self) -> None:
        server = self._server
        for attempt, delay in enumerate(RETRY_DELAYS, start=1):
            await asyncio.sleep(delay)
            if self._manual_disconnect or server is None:
                return
            self._logger.info(f"[xray-vpn-decky] reconnect attempt {attempt}/{MAX_RETRIES}")
            self._set_state(STATE_CONNECTING, f"Переподключение ({attempt}/{MAX_RETRIES})...")
            try:
                await self.connect(server)
                return
            except XrayError as exc:
                self._logger.error(f"[xray-vpn-decky] reconnect failed: {exc}")
        self._set_state(STATE_ERROR, "Не удалось переподключиться после 3 попыток")

    async def disconnect(self, silent: bool = False) -> None:
        self._logger.debug(f"[xray-vpn-decky] disconnect (silent={silent})")
        self._manual_disconnect = True
        if self._retry_task and not self._retry_task.done():
            self._retry_task.cancel()
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
        proc = self._proc
        self._proc = None
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5)
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
        self._server = None
        self._session_started = 0.0
        if not silent:
            self._set_state(STATE_DISCONNECTED)

    async def stop_all(self) -> None:
        """Корректная остановка при выгрузке плагина / перезагрузке Steam."""
        await self.disconnect(silent=True)
        self._set_state(STATE_DISCONNECTED)
