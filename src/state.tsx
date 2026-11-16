import { createContext, useContext, useEffect, useRef, useState } from "react";
import { toaster } from "@decky/api";
import { backend, VpnState, PluginSettings } from "./backend";

const POLL_MS = 1500;

const FALLBACK_STATE: VpnState = {
  state: "disconnected",
  error: null,
  server: null,
  session_seconds: 0,
  xray_ready: false,
  xray_version: null,
  subscriptions: [],
  active_subscription_id: null,
  servers: [],
  selected_server_id: null,
  has_subscriptions: false,
};

const FALLBACK_SETTINGS: PluginSettings = {
  auto_connect_on_start: false,
  auto_connect_min_ping: false,
  reconnect_on_switch: true,
};

interface VpnContextValue {
  state: VpnState;
  settings: PluginSettings;
  refresh: () => Promise<void>;
  refreshSettings: () => Promise<void>;
  busy: string | null;
  run: (op: string, fn: () => Promise<unknown>) => Promise<void>;
}

const VpnContext = createContext<VpnContextValue>({
  state: FALLBACK_STATE,
  settings: FALLBACK_SETTINGS,
  refresh: async () => {},
  refreshSettings: async () => {},
  busy: null,
  run: async () => {},
});

export function errText(e: unknown): string {
  if (e instanceof Error) return e.message || String(e);
  return typeof e === "string" ? e : JSON.stringify(e);
}

export const VpnProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [state, setState] = useState<VpnState>(FALLBACK_STATE);
  const [settings, setSettings] = useState<PluginSettings>(FALLBACK_SETTINGS);
  const [busy, setBusy] = useState<string | null>(null);
  const busyRef = useRef(false);

  const refresh = async () => {
    try {
      setState(await backend.getState());
    } catch (e) {
      console.error("[xray-vpn-decky] get_state failed", e);
    }
  };

  const refreshSettings = async () => {
    try {
      setSettings(await backend.getSettings());
    } catch (e) {
      console.error("[xray-vpn-decky] get_settings failed", e);
    }
  };

  useEffect(() => {
    refresh();
    refreshSettings();
    const t = window.setInterval(refresh, POLL_MS);
    return () => window.clearInterval(t);
  }, []);

  const run = async (op: string, fn: () => Promise<unknown>) => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(op);
    try {
      await fn();
      await refresh();
    } catch (e) {
      toaster.toast({ title: "Xray VPN Decky", body: errText(e) });
    } finally {
      busyRef.current = false;
      setBusy(null);
    }
  };

  return (
    <VpnContext.Provider value={{ state, settings, refresh, refreshSettings, busy, run }}>
      {children}
    </VpnContext.Provider>
  );
};

export const useVpn = () => useContext(VpnContext);

export function formatDuration(totalSeconds: number): string {
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

export function formatDate(ts: number): string {
  if (!ts) return "—";
  try {
    return new Date(ts * 1000).toLocaleDateString();
  } catch {
    return "—";
  }
}

/** Флаг в начале названия сервера (если есть), иначе 🌐. */
export function flagOf(name: string): string {
  const m = name.match(/^\p{Regional_Indicator}{2}/u);
  return m ? m[0] : "🌐";
}

export function stripFlag(name: string): string {
  return name.replace(/^\p{Regional_Indicator}{2}\s*/u, "").trim() || name;
}
