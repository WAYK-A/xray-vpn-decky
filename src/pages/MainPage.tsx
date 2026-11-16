import { DialogButton, Focusable, Navigation, PanelSection, PanelSectionRow, Spinner } from "@decky/ui";
import { FaPlug, FaServer } from "react-icons/fa";
import { backend } from "../backend";
import { flagOf, formatDuration, stripFlag, useVpn } from "../state";

const STATUS_LABEL: Record<string, string> = {
  disconnected: "Не подключено",
  connecting: "Подключение…",
  connected: "Подключено",
  error: "Ошибка",
};

const STATUS_COLOR: Record<string, string> = {
  disconnected: "#9a9a9a",
  connecting: "#e3b341",
  connected: "#3fb950",
  error: "#f85149",
};

export default function MainPage() {
  const { state, busy, run } = useVpn();
  const connected = state.state === "connected";
  const connecting = state.state === "connecting";
  const selected = state.servers.find((s) => s.id === state.selected_server_id) ?? null;

  const connectBtn = (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", padding: "16px 0 20px" }}>
      <DialogButton
        style={{
          width: 240,
          height: 72,
          fontSize: 22,
          fontWeight: 600,
          borderRadius: 36,
          background: connecting
            ? "#8a6d1f"
            : connected
              ? "#1a7f37"
              : "#2a475e",
          color: "#fff",
        }}
        disabled={busy !== null || (!state.has_subscriptions && !connected && !connecting)}
        onClick={() =>
          run(connected || connecting ? "disconnect" : "connect", () =>
            connected || connecting ? backend.disconnect() : backend.connect(),
          )
        }
      >
        {busy === "connect" || busy === "disconnect" ? (
          <Spinner style={{ width: 28, height: 28, color: "#fff" }} />
        ) : connecting ? (
          "Подключение…"
        ) : connected ? (
          "Отключить"
        ) : (
          "Подключить"
        )}
      </DialogButton>
      <div style={{ marginTop: 10, fontSize: 15, color: STATUS_COLOR[state.state] }}>
        {STATUS_LABEL[state.state]}
        {connected && state.session_seconds > 0 && ` · ${formatDuration(state.session_seconds)}`}
      </div>
      {state.error && (
        <div style={{ marginTop: 6, fontSize: 13, color: "#f85149", maxWidth: 320, textAlign: "center" }}>
          {state.error}
        </div>
      )}
    </div>
  );

  return (
    <PanelSection title="Xray VPN Decky">
      {connectBtn}

      {!state.has_subscriptions ? (
        <PanelSectionRow>
          <Focusable style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 10, padding: "8px 0" }}>
            <div style={{ fontSize: 14, color: "#c9c9c9", textAlign: "center" }}>
              Подписок пока нет. Добавьте свою подписку Happ/v2rayTUN — и выберите сервер.
            </div>
            <DialogButton
              style={{ width: 260 }}
              onClick={() => Navigation.Navigate("/xray-vpn-decky/subscriptions")}
            >
              Добавить подписку
            </DialogButton>
          </Focusable>
        </PanelSectionRow>
      ) : (
        <>
          {selected && (
            <PanelSectionRow>
              <Focusable
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 12,
                  padding: "10px 12px",
                  background: "rgba(255,255,255,0.06)",
                  borderRadius: 4,
                }}
              >
                <span style={{ fontSize: 26 }}>{flagOf(selected.name)}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 15, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                    {stripFlag(selected.name)}
                  </div>
                  <div style={{ fontSize: 12, color: "#8b929a" }}>
                    {selected.host}:{selected.port}
                    {selected.ping != null && ` · ${selected.ping} мс`}
                  </div>
                </div>
                {(connected || connecting) && <FaServer style={{ color: STATUS_COLOR[state.state] }} />}
              </Focusable>
            </PanelSectionRow>
          )}
          <PanelSectionRow>
            <Focusable style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <DialogButton style={{ height: 42 }} onClick={() => Navigation.Navigate("/xray-vpn-decky/servers")}>
                <FaServer style={{ marginRight: 8, verticalAlign: -2 }} />
                Серверы
              </DialogButton>
              <DialogButton style={{ height: 42 }} onClick={() => Navigation.Navigate("/xray-vpn-decky/subscriptions")}>
                Подписки
              </DialogButton>
              <DialogButton style={{ height: 42 }} onClick={() => Navigation.Navigate("/xray-vpn-decky/settings")}>
                Настройки
              </DialogButton>
            </Focusable>
          </PanelSectionRow>
        </>
      )}

      {!state.xray_ready && state.has_subscriptions && (
        <PanelSectionRow>
          <DialogButton
            onClick={() =>
              run("ensure_xray", async () => {
                await backend.ensureXray();
              })
            }
          >
            <FaPlug style={{ marginRight: 8, verticalAlign: -2 }} />
            {busy === "ensure_xray" ? "Загрузка ядра… (~20 МБ)" : "Загрузить ядро Xray"}
          </DialogButton>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
}
