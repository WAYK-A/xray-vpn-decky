import { callable } from "@decky/api";

export type ConnState = "disconnected" | "connecting" | "connected" | "error";

export interface ServerInfo {
  id: string;
  name: string;
  host: string;
  port: number;
  ping?: number | null;
}

export interface SubInfo {
  id: string;
  name: string;
  url: string;
  added_at: number;
  updated_at: number;
  server_count: number;
  selected_server: string | null;
}

export interface VpnState {
  state: ConnState;
  error: string | null;
  server: ServerInfo | null;
  session_seconds: number;
  xray_ready: boolean;
  xray_version: string | null;
  subscriptions: SubInfo[];
  active_subscription_id: string | null;
  servers: ServerInfo[];
  selected_server_id: string | null;
  has_subscriptions: boolean;
}

export interface PluginSettings {
  auto_connect_on_start: boolean;
  auto_connect_min_ping: boolean;
  reconnect_on_switch: boolean;
}

const c = <Args extends any[] = [], Return = void>(name: string) =>
  callable<Args, Return>(name);

export const backend = {
  getState: c<[], VpnState>("get_state"),
  getSettings: c<[], PluginSettings>("get_settings"),
  setSetting: c<[key: keyof PluginSettings, value: boolean], PluginSettings>("set_setting"),

  addSubscription: c<[urlOrB64: string, name?: string], unknown>("add_subscription"),
  updateSubscription: c<[subId: string], unknown>("update_subscription"),
  renameSubscription: c<[subId: string, name: string], void>("rename_subscription"),
  removeSubscription: c<[subId: string], VpnState>("remove_subscription"),
  setActiveSubscription: c<[subId: string], VpnState>("set_active_subscription"),

  selectServer: c<[serverId: string], VpnState>("select_server"),
  pingAll: c<[], Record<string, number | null>>("ping_all"),

  connect: c<[], VpnState>("connect"),
  disconnect: c<[], VpnState>("disconnect"),
  ensureXray: c<[], { xray_ready: boolean; xray_version: string | null }>("ensure_xray"),
  getLogs: c<[maxLines?: number], string>("get_logs"),
};
