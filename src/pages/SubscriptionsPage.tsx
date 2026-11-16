import { useState } from "react";
import {
  ConfirmModal,
  DialogButton,
  Focusable,
  ModalRoot,
  PanelSection,
  PanelSectionRow,
  showModal,
  Spinner,
  TextField,
} from "@decky/ui";
import { FaCheck, FaPen, FaPlus, FaSyncAlt, FaTrashAlt } from "react-icons/fa";
import { backend, SubInfo } from "../backend";
import { errText, formatDate, useVpn } from "../state";
import BackButton from "../components/BackButton";

function AddSubscriptionModal({ closeModal, onAdded }: { closeModal: () => void; onAdded: () => void }) {
  const [value, setValue] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState("");
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    if (!value.trim() || busy) return;
    setBusy(true);
    setError(null);
    setStage("Загрузка и разбор подписки…");
    try {
      // Страховка: даже если вызов бэкенда «завис», диалог покажет ошибку, а не спиннер вечно.
      await Promise.race([
        backend.addSubscription(value.trim(), name.trim()),
        new Promise<never>((_, reject) =>
          window.setTimeout(
            () => reject(new Error("Бэкенд не ответил за 40 с. Откройте Настройки → Показать логи и отправьте лог разработчику.")),
            40000,
          ),
        ),
      ]);
      onAdded();
      closeModal();
    } catch (e) {
      setError(errText(e));
      setStage("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <ModalRoot closeModal={closeModal}>
      <div style={{ minWidth: 420, maxWidth: 480, display: "flex", flexDirection: "column", gap: 10, padding: 4 }}>
        <div style={{ fontSize: 16, fontWeight: 600 }}>Добавить подписку</div>
        <TextField
          label="Название (необязательно)"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Например: Моя подписка"
        />
        <TextField
          label="Ссылка на подписку или base64"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="https://…/sub  или  dmxlc3M6Ly8uLi4="
        />
        {stage && !error && (
          <div style={{ display: "flex", alignItems: "center", gap: 8, color: "#c9c9c9", fontSize: 13 }}>
            <Spinner style={{ width: 14, height: 14 }} />
            {stage}
          </div>
        )}
        {error && <div style={{ color: "#f85149", fontSize: 13, whiteSpace: "pre-wrap" }}>{error}</div>}
        <Focusable style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <DialogButton
            style={{ width: "100%" }}
            disabled={!value.trim() || busy}
            onClick={() => void save()}
          >
            {busy ? <Spinner style={{ width: 16, height: 16 }} /> : "Сохранить"}
          </DialogButton>
          <DialogButton style={{ width: "100%" }} disabled={busy} onClick={closeModal}>
            Отмена
          </DialogButton>
        </Focusable>
      </div>
    </ModalRoot>
  );
}

function RenameModal({ sub, closeModal, onRenamed }: { sub: SubInfo; closeModal: () => void; onRenamed: () => void }) {
  const [value, setValue] = useState(sub.name);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    if (!value.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      await backend.renameSubscription(sub.id, value.trim());
      onRenamed();
      closeModal();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <ModalRoot closeModal={closeModal}>
      <div style={{ minWidth: 380, display: "flex", flexDirection: "column", gap: 10, padding: 4 }}>
        <div style={{ fontSize: 16, fontWeight: 600 }}>Переименовать</div>
        <TextField label="Название" value={value} onChange={(e) => setValue(e.target.value)} />
        {error && <div style={{ color: "#f85149", fontSize: 13 }}>{error}</div>}
        <Focusable style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <DialogButton style={{ width: "100%" }} disabled={!value.trim() || busy} onClick={() => void save()}>
            {busy ? <Spinner style={{ width: 16, height: 16 }} /> : "Сохранить"}
          </DialogButton>
          <DialogButton style={{ width: "100%" }} disabled={busy} onClick={closeModal}>
            Отмена
          </DialogButton>
        </Focusable>
      </div>
    </ModalRoot>
  );
}

function openAddModal(onAdded: () => void) {
  let modal: ReturnType<typeof showModal>;
  modal = showModal(<AddSubscriptionModal closeModal={() => modal.Close()} onAdded={onAdded} />);
}

function openRenameModal(sub: SubInfo, onRenamed: () => void) {
  let modal: ReturnType<typeof showModal>;
  modal = showModal(<RenameModal sub={sub} closeModal={() => modal.Close()} onRenamed={onRenamed} />);
}

function openDeleteModal(sub: SubInfo, onDeleted: () => void) {
  let modal: ReturnType<typeof showModal>;
  modal = showModal(
    <ConfirmModal
      closeModal={() => modal.Close()}
      strTitle="Удалить подписку?"
      strDescription={`«${sub.name}» будет удалена безвозвратно.`}
      onOK={() => {
        modal.Close();
        onDeleted();
      }}
      strOKButtonText="Удалить"
    />,
  );
}

function SubRow({ sub, active }: { sub: SubInfo; active: boolean }) {
  const { busy, run, refresh } = useVpn();

  return (
    <PanelSectionRow>
      <Focusable
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 4,
          padding: "8px 10px",
          borderRadius: 4,
          background: active ? "rgba(59,185,80,0.12)" : "rgba(255,255,255,0.05)",
          border: active ? "1px solid rgba(59,185,80,0.5)" : undefined,
        }}
      >
        <DialogButton
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "4px 6px",
            height: 36,
            background: "transparent",
          }}
          disabled={busy !== null}
          onClick={() => !active && run("activate", () => backend.setActiveSubscription(sub.id).then(refresh))}
        >
          <span style={{ flex: 1, textAlign: "left", fontSize: 14, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
            {sub.name}
          </span>
          {active && <FaCheck color="#3fb950" />}
        </DialogButton>
        <div style={{ fontSize: 12, color: "#8b929a", paddingLeft: 6 }}>
          {sub.server_count} серверов · обновлена {formatDate(sub.updated_at)}
          {sub.url ? " · URL" : " · base64"}
        </div>
        <Focusable style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <DialogButton
            style={{ height: 30, fontSize: 13, minWidth: 0, padding: 0 }}
            disabled={busy !== null || !sub.url}
            onClick={() => run("update", () => backend.updateSubscription(sub.id).then(refresh))}
          >
            {busy === "update" ? <Spinner style={{ width: 12, height: 12 }} /> : <><FaSyncAlt style={{ marginRight: 6 }} />Обновить</>}
          </DialogButton>
          <DialogButton
            style={{ height: 30, fontSize: 13, minWidth: 0, padding: 0 }}
            disabled={busy !== null}
            onClick={() => openRenameModal(sub, () => void refresh())}
          >
            <FaPen style={{ marginRight: 6 }} />Переименовать
          </DialogButton>
          <DialogButton
            style={{ height: 30, fontSize: 13, minWidth: 0, padding: 0, color: "#f85149" }}
            disabled={busy !== null}
            onClick={() => openDeleteModal(sub, () => void run("remove", () => backend.removeSubscription(sub.id).then(refresh)))}
          >
            <FaTrashAlt style={{ marginRight: 6 }} />Удалить
          </DialogButton>
        </Focusable>
      </Focusable>
    </PanelSectionRow>
  );
}

export default function SubscriptionsPage() {
  const { state, refresh } = useVpn();

  return (
    <PanelSection title="Подписки">
      <BackButton />
      <PanelSectionRow>
        <DialogButton onClick={() => openAddModal(() => void refresh())}>
          <FaPlus style={{ marginRight: 8, verticalAlign: -2 }} />
          Добавить подписку
        </DialogButton>
      </PanelSectionRow>

      {state.subscriptions.map((sub) => (
        <SubRow key={sub.id} sub={sub} active={sub.id === state.active_subscription_id} />
      ))}

      {state.subscriptions.length === 0 && (
        <PanelSectionRow>
          <Focusable style={{ padding: 8, textAlign: "center", color: "#8b929a" }}>
            Подписок нет. Вставьте ссылку (URL) на свою подписку или её base64-содержимое.
          </Focusable>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
}
