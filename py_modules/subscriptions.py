"""Хранение и обновление подписок пользователя."""

from __future__ import annotations

import json
import re
import time
import uuid as uuidlib
from pathlib import Path
from typing import Any

import aiohttp

from .vless import (
    VlessParseError,
    extract_links_from_subscription,
    looks_like_json,
    parse_full_config_subscription,
    parse_subscription_links,
)

FETCH_TIMEOUT = aiohttp.ClientTimeout(total=20, connect=10)
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


class SubscriptionError(Exception):
    """Понятная пользователю ошибка работы с подпиской."""


class SubscriptionManager:
    def __init__(self, settings_dir: Path, logger) -> None:
        self._settings_dir = settings_dir
        self._path = settings_dir / "subscriptions.json"
        self._logger = logger
        self._subs: list[dict[str, Any]] = []
        self._active_id: str | None = None
        self._load()

    # ---------- persistence ----------

    def _load(self) -> None:
        try:
            if self._path.exists():
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self._subs = list(data.get("subscriptions", []))
                self._active_id = data.get("active_id")
                ids = {s.get("id") for s in self._subs}
                if self._active_id not in ids:
                    self._active_id = self._subs[0]["id"] if self._subs else None
        except Exception as exc:  # noqa: BLE001
            self._logger.error(f"[xray-vpn-decky] failed to load subscriptions: {exc}")
            self._subs = []
            self._active_id = None

    def _save(self) -> None:
        self._settings_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {"active_id": self._active_id, "subscriptions": self._subs},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        tmp.replace(self._path)
        self._logger.debug(f"[xray-vpn-decky] subscriptions saved: {len(self._subs)} subs")

    # ---------- public getters ----------

    def list(self) -> list[dict[str, Any]]:
        """Список подписок без секретных полей серверов (uuid не отдаём в логи, в UI не нужен)."""
        return [
            {
                "id": s["id"],
                "name": s["name"],
                "url": s.get("url", ""),
                "added_at": s.get("added_at", 0),
                "updated_at": s.get("updated_at", 0),
                "server_count": len(s.get("servers", [])),
                "selected_server": s.get("selected_server"),
            }
            for s in self._subs
        ]

    def get(self, sub_id: str) -> dict[str, Any] | None:
        for s in self._subs:
            if s["id"] == sub_id:
                return s
        return None

    def active(self) -> dict[str, Any] | None:
        if self._active_id:
            sub = self.get(self._active_id)
            if sub:
                return sub
        return self._subs[0] if self._subs else None

    def active_id(self) -> str | None:
        sub = self.active()
        return sub["id"] if sub else None

    def active_servers(self) -> list[dict[str, Any]]:
        sub = self.active()
        return list(sub.get("servers", [])) if sub else []

    def selected_server(self) -> dict[str, Any] | None:
        sub = self.active()
        if not sub:
            return None
        sel = sub.get("selected_server")
        for server in sub.get("servers", []):
            if server["id"] == sel:
                return server
        servers = sub.get("servers", [])
        return servers[0] if servers else None

    # ---------- mutations ----------

    async def fetch_content(self, url_or_b64: str) -> tuple[list[dict[str, Any]], list[str]]:
        """Получить и распарсить содержимое подписки (по URL или из base64/plain-текста)."""
        content: str
        if _URL_RE.match(url_or_b64.strip()):
            self._logger.info("[xray-vpn-decky] fetch: downloading subscription...")
            try:
                async with aiohttp.ClientSession(timeout=FETCH_TIMEOUT) as session:
                    async with session.get(url_or_b64.strip()) as resp:
                        if resp.status != 200:
                            raise SubscriptionError(
                                f"Сервер подписки вернул ошибку HTTP {resp.status}"
                            )
                        raw = await resp.read()
                        content = raw.decode("utf-8", errors="replace")
                self._logger.info(f"[xray-vpn-decky] fetch: downloaded {len(content)} chars")
            except SubscriptionError:
                raise
            except aiohttp.ClientError as exc:
                raise SubscriptionError(f"Сетевая ошибка при загрузке подписки: {exc}") from exc
            except Exception as exc:  # noqa: BLE001
                raise SubscriptionError(f"Не удалось загрузить подписку: {exc}") from exc
        else:
            content = url_or_b64

        try:
            if looks_like_json(content):
                # Формат v2rayN/911vpn: JSON со списком полных конфигов Xray.
                servers, errors = parse_full_config_subscription(content)
            else:
                links = extract_links_from_subscription(content)
                servers, errors = parse_subscription_links(links)
        except VlessParseError as exc:
            raise SubscriptionError(str(exc)) from exc
        self._logger.info(f"[xray-vpn-decky] fetch: parsed {len(servers)} servers")
        if not servers:
            detail = "; ".join(errors[:3]) if errors else "нет поддерживаемых ссылок"
            raise SubscriptionError(f"В подписке нет поддерживаемых vless-серверов: {detail}")
        return servers, errors

    async def add(self, url_or_b64: str, name: str | None = None) -> dict[str, Any]:
        url_or_b64 = url_or_b64.strip()
        if not url_or_b64:
            raise SubscriptionError("Пустая ссылка или содержимое подписки")
        servers, _errors = await self.fetch_content(url_or_b64)

        is_url = bool(_URL_RE.match(url_or_b64))
        sub: dict[str, Any] = {
            "id": uuidlib.uuid4().hex[:12],
            "name": (name or "").strip() or self._guess_name(servers),
            "url": url_or_b64 if is_url else "",
            "added_at": int(time.time()),
            "updated_at": int(time.time()),
            "servers": servers,
            "selected_server": servers[0]["id"],
        }
        self._subs.append(sub)
        if self._active_id is None:
            self._active_id = sub["id"]
        self._save()
        return sub

    async def update(self, sub_id: str) -> dict[str, Any]:
        sub = self.get(sub_id)
        if not sub:
            raise SubscriptionError("Подписка не найдена")
        source = sub.get("url") or ""
        if not source:
            # Подписка добавлена как base64-текст: обновлять нечего, возвращаем как есть.
            return sub
        servers, _errors = await self.fetch_content(source)
        sub["servers"] = servers
        sub["updated_at"] = int(time.time())
        if sub.get("selected_server") not in {s["id"] for s in servers}:
            sub["selected_server"] = servers[0]["id"]
        self._save()
        return sub

    def rename(self, sub_id: str, name: str) -> None:
        sub = self.get(sub_id)
        if not sub:
            raise SubscriptionError("Подписка не найдена")
        name = name.strip()
        if not name:
            raise SubscriptionError("Название не может быть пустым")
        sub["name"] = name
        self._save()

    def remove(self, sub_id: str) -> bool:
        """Удалить подписку. Возвращает True, если удалена активная."""
        before = len(self._subs)
        self._subs = [s for s in self._subs if s["id"] != sub_id]
        if len(self._subs) == before:
            raise SubscriptionError("Подписка не найдена")
        was_active = self._active_id == sub_id
        if was_active:
            self._active_id = self._subs[0]["id"] if self._subs else None
        self._save()
        return was_active

    def set_active(self, sub_id: str) -> None:
        if not self.get(sub_id):
            raise SubscriptionError("Подписка не найдена")
        self._active_id = sub_id
        self._save()

    def select_server(self, server_id: str, sub_id: str | None = None) -> None:
        sub = self.get(sub_id) if sub_id else self.active()
        if not sub:
            raise SubscriptionError("Подписка не найдена")
        if not any(s["id"] == server_id for s in sub.get("servers", [])):
            raise SubscriptionError("Сервер не найден в подписке")
        sub["selected_server"] = server_id
        self._save()

    def update_pings(self, pings: dict[str, int | None]) -> None:
        for sub in self._subs:
            for server in sub.get("servers", []):
                ping = pings.get(server["id"])
                if ping is not None:
                    server["ping"] = ping
                elif server["id"] in pings:
                    server.pop("ping", None)
        self._save()

    @staticmethod
    def _guess_name(servers: list[dict[str, Any]]) -> str:
        if not servers:
            return "Подписка"
        first = servers[0]["name"]
        # «🇩🇪 Germany 01» → «Germany»: убираем флаг (пара regional indicators) и хвост из цифр.
        cleaned = re.sub("^[\U0001F1E6-\U0001F1FF]{2}\\s*", "", first).strip()
        parts = cleaned.split()
        if parts:
            return parts[0][:24]
        return "Подписка"
