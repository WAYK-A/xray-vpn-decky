import { definePlugin, staticClasses } from "@decky/ui";
import { FaShieldAlt } from "react-icons/fa";
import { routerHook } from "@decky/api";
import { VpnProvider } from "./state";
import MainPage from "./pages/MainPage";
import ServersPage from "./pages/ServersPage";
import SubscriptionsPage from "./pages/SubscriptionsPage";
import SettingsPage from "./pages/SettingsPage";

const Content = () => {
  return (
    <VpnProvider>
      <MainPage />
    </VpnProvider>
  );
};

// Полноэкранные страницы (вертикальная колонка во всю ширину, без сайдбара)
routerHook.addRoute("/xray-vpn-decky/servers", () => (
  <VpnProvider>
    <ServersPage />
  </VpnProvider>
));
routerHook.addRoute("/xray-vpn-decky/subscriptions", () => (
  <VpnProvider>
    <SubscriptionsPage />
  </VpnProvider>
));
routerHook.addRoute("/xray-vpn-decky/settings", () => (
  <VpnProvider>
    <SettingsPage />
  </VpnProvider>
));

export default definePlugin(() => {
  return {
    title: <div className={staticClasses.Title}>Xray VPN Decky</div>,
    content: <Content />,
    icon: <FaShieldAlt />,
    onDismount() {
      // опрос состояния останавливается размонтированием VpnProvider
    },
  };
});
