import { useState } from "react";
import { DialogButton,
  ModalRoot,
  Navigation,
  PanelSection,
  PanelSectionRow,
  Spinner,
  ToggleField,
} from "@decky/ui";
import { FaDownload, FaFileAlt } from "react-icons/fa";
import { backend, PluginSettings } from "../backend";
import { errText, useVpn } from "../state";
import BackButton from "../components/BackButton";

function LogsModal({ logs, closeModal }: { logs: string; closeModal: () => void }) {
  return (
    <ModalRoot closeModal={closeModal}>
      <div style={{ minWidth: 520, maxWidth: 640, display: "flex", flexDirection: "column", gap: 10, padding: 4 }}>
        <div style={{ fontSize: 16, fontWeight: 600 }}>Логи</div>
        <div
          style={{
            maxHeight: 320,
            overflowY: "auto",
            background: "rgba(0,0,0,0.4)",
            borderRadius: 4,
            padding: 8,
            fontFamily: "monospace",
            fontSize: 11,
            whiteSpace: "pre-wrap",
            wordBreak: "break-all",
          }}
        >
          {logs || "Логи пусты"}
        </div>
        <DialogButton style={{ width: "100%" }} onClick={closeModal}>
          Закрыть
        </DialogButton>
      </div>
    </ModalRoot>
  );
}

export default function SettingsPage() {
  const { state, settings, refreshSettings, busy, run } = useVpn();
  const [logs, setLogs] = useState<string | null>(null);

  const toggle = (key: keyof PluginSettings, label: string, description?: string) => (
    <PanelSectionRow>
      <ToggleField
        label={label}
        description={description}
        checked={settings[key]}
        onChange={async (value) => {
          try {
            await backend.setSetting(key, value);
            await refreshSettings();
          } catch (e) {
            console.error("[xray-vpn-decky] set_setting failed", e);
          }
        }}
      />
    </PanelSectionRow>
  );

  const openLogs = async () => {
    try {
      setLogs(await backend.getLogs(300));
    } catch (e) {
      setLogs(`Не удалось прочитать логи: ${errText(e)}`);
    }
  };

  return (
    <>
      <PanelSection title="Настройки">
        <BackButton />
      </PanelSection>
      <PanelSection title="Подключение">
        {toggle("auto_connect_on_start", "Автоподключение при старте", "Подключаться к выбранному серверу при запуске Steam")}
        {toggle("auto_connect_min_ping", "Сервер с минимальным пингом", "При автоподключении выбирать самый быстрый сервер")}
        {toggle("reconnect_on_switch", "Переподключаться при смене сервера", "Немедленно переподключать VPN при выборе другого сервера")}
      </PanelSection>

      <PanelSection title="Ядро Xray">
        <PanelSectionRow>
          <ToggleField
            label="Ядро загружено"
            checked={state.xray_ready}
            disabled
            onChange={() => {}}
          />
        </PanelSectionRow>
        {state.xray_version && (
          <PanelSectionRow>
            <div style={{ fontSize: 12, color: "#8b929a", paddingLeft: 16 }}>
              Версия: {state.xray_version}
            </div>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <DialogButton
            disabled={busy !== null || state.xray_ready}
            onClick={() => run("ensure_xray", () => backend.ensureXray())}
          >
            {busy === "ensure_xray" ? (
              <Spinner style={{ width: 16, height: 16 }} />
            ) : (
              <><FaDownload style={{ marginRight: 8, verticalAlign: -2 }} />Загрузить ядро сейчас (~20 МБ)</>
            )}
          </DialogButton>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Диагностика">
        <PanelSectionRow>
          <DialogButton onClick={() => void openLogs()}>
            <FaFileAlt style={{ marginRight: 8, verticalAlign: -2 }} />
            Показать логи
          </DialogButton>
        </PanelSectionRow>
        <PanelSectionRow>
          <DialogButton onClick={() => Navigation.Navigate("/xray-vpn-decky/subscriptions")}>
            Управление подписками
          </DialogButton>
        </PanelSectionRow>
      </PanelSection>

      {logs !== null && <LogsModal logs={logs} closeModal={() => setLogs(null)} />}
    </>
  );
}
