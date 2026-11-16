import { useMemo, useState } from "react";
import {
  DialogButton,
  DropdownItem,
  Focusable,
  Navigation,
  PanelSection,
  PanelSectionRow,
  Spinner,
  TextField,
} from "@decky/ui";
import { FaCheckCircle, FaExchangeAlt, FaSyncAlt } from "react-icons/fa";
import { backend, ServerInfo } from "../backend";
import { flagOf, stripFlag, useVpn } from "../state";
import BackButton from "../components/BackButton";

type SortMode = "default" | "ping" | "name";

function ServerRow({
  server,
  selected,
  onSelect,
  disabled,
}: {
  server: ServerInfo;
  selected: boolean;
  onSelect: () => void;
  disabled: boolean;
}) {
  return (
    <PanelSectionRow>
      <DialogButton
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "8px 10px",
          height: 44,
          background: selected ? "rgba(59,185,80,0.15)" : undefined,
          border: selected ? "1px solid rgba(59,185,80,0.5)" : undefined,
        }}
        disabled={disabled}
        onClick={onSelect}
      >
        <span style={{ fontSize: 20 }}>{flagOf(server.name)}</span>
        <span
          style={{
            flex: 1,
            textAlign: "left",
            fontSize: 14,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {stripFlag(server.name)}
        </span>
        {server.ping != null && (
          <span style={{ fontSize: 12, color: server.ping < 150 ? "#3fb950" : server.ping < 300 ? "#e3b341" : "#f85149" }}>
            {server.ping} мс
          </span>
        )}
        {selected && <FaCheckCircle color="#3fb950" />}
      </DialogButton>
    </PanelSectionRow>
  );
}

export default function ServersPage() {
  const { state, busy, run, refresh } = useVpn();
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<SortMode>("default");

  const activeSub = state.subscriptions.find((s) => s.id === state.active_subscription_id);

  const servers = useMemo(() => {
    let list = [...state.servers];
    const q = query.trim().toLowerCase();
    if (q) {
      list = list.filter(
        (s) => s.name.toLowerCase().includes(q) || s.host.toLowerCase().includes(q),
      );
    }
    if (sort === "ping") {
      list.sort((a, b) => (a.ping ?? 99999) - (b.ping ?? 99999));
    } else if (sort === "name") {
      list.sort((a, b) => stripFlag(a.name).localeCompare(stripFlag(b.name)));
    }
    return list;
  }, [state.servers, query, sort]);

  if (!state.has_subscriptions) {
    return (
      <PanelSection title="Серверы">
        <BackButton />
        <PanelSectionRow>
          <Focusable style={{ padding: "12px 0", textAlign: "center", color: "#8b929a" }}>
            Сначала добавьте подписку на экране «Подписки».
          </Focusable>
        </PanelSectionRow>
        <PanelSectionRow>
          <DialogButton onClick={() => Navigation.Navigate("/xray-vpn-decky/subscriptions")}>
            Перейти к подпискам
          </DialogButton>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  return (
    <PanelSection title="Серверы">
      <BackButton />
      <PanelSectionRow>
        <Focusable
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "8px 10px",
            background: "rgba(255,255,255,0.05)",
            borderRadius: 4,
            fontSize: 13,
          }}
        >
          <FaExchangeAlt style={{ flexShrink: 0 }} />
          <span style={{ flex: 1, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
            {activeSub?.name ?? "—"} · {state.servers.length} серверов
          </span>
        </Focusable>
      </PanelSectionRow>

      <PanelSectionRow>
        <TextField
          label="Поиск"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Страна или название"
        />
      </PanelSectionRow>

      <PanelSectionRow>
        <DropdownItem
          label="Сортировка"
          rgOptions={[
            { label: "Как в подписке", data: "default" as SortMode },
            { label: "По пингу", data: "ping" as SortMode },
            { label: "По названию", data: "name" as SortMode },
          ]}
          selectedOption={sort}
          onChange={(o) => setSort(o.data as SortMode)}
        />
      </PanelSectionRow>

      <PanelSectionRow>
        <Focusable style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <DialogButton
            style={{ height: 36, fontSize: 14 }}
            disabled={busy !== null}
            onClick={() =>
              run("refresh_sub", async () => {
                if (state.active_subscription_id) {
                  await backend.updateSubscription(state.active_subscription_id);
                }
              })
            }
          >
            {busy === "refresh_sub" ? <Spinner style={{ width: 16, height: 16 }} /> : <><FaSyncAlt style={{ marginRight: 8, verticalAlign: -2 }} />Обновить подписку</>}
          </DialogButton>
          <DialogButton
            style={{ height: 36, fontSize: 14 }}
            disabled={busy !== null || state.servers.length === 0}
            onClick={() => run("ping", () => backend.pingAll().then(() => {}))}
          >
            {busy === "ping" ? <Spinner style={{ width: 16, height: 16 }} /> : "Замерить пинг всех серверов"}
          </DialogButton>
          <DialogButton
            style={{ height: 36, fontSize: 14 }}
            onClick={() => Navigation.Navigate("/xray-vpn-decky/subscriptions")}
          >
            Перейти к подпискам
          </DialogButton>
        </Focusable>
      </PanelSectionRow>

      {servers.map((server) => (
        <ServerRow
          key={server.id}
          server={server}
          selected={server.id === state.selected_server_id}
          disabled={busy !== null}
          onSelect={() => run("select", () => backend.selectServer(server.id).then(refresh))}
        />
      ))}

      {servers.length === 0 && (
        <PanelSectionRow>
          <Focusable style={{ padding: 8, textAlign: "center", color: "#8b929a" }}>
            Ничего не найдено
          </Focusable>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
}
