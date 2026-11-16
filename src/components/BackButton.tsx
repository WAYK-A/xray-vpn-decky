import { DialogButton, Navigation, PanelSectionRow } from "@decky/ui";
import { FaArrowLeft } from "react-icons/fa";

export default function BackButton() {
  return (
    <PanelSectionRow>
      <DialogButton
        style={{ height: 34, fontSize: 13, display: "flex", alignItems: "center", gap: 8 }}
        onClick={() => Navigation.NavigateBack()}
      >
        <FaArrowLeft />
        Назад
      </DialogButton>
    </PanelSectionRow>
  );
}
