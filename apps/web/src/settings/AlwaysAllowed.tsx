import React, { useEffect, useState } from "react";
import { leanApi } from "../lean/api";
import { Row } from "./controls";

type Grant = { key: string; label: string; created_at: number };

/** The "Always allow" grants the owner made from approval cards: visible and removable in one place. */
export function AlwaysAllowed({ apiBase }: { apiBase: string }) {
  const [items, setItems] = useState<Grant[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    leanApi(apiBase).approvalGrants().then((data) => live && setItems(data.items || [])).catch(() => live && setItems([]));
    return () => { live = false; };
  }, [apiBase]);

  const remove = async (key: string) => {
    setError("");
    try {
      await leanApi(apiBase).removeApprovalGrant(key);
      setItems((rows) => (rows || []).filter((row) => row.key !== key));
    } catch (e) {
      setError(`Couldn't remove it: ${e instanceof Error ? e.message : "try again"}.`);
    }
  };

  return (
    <Row
      label="Always allowed"
      help={items && items.length
        ? "Approvals you chose Always allow for. They apply in every chat until you remove them. Deleting, dangerous commands, sending messages and tasks for other agents always ask."
        : "Nothing yet. Choose Always allow on an approval to stop being asked about that kind of action."}
      stack
    >
      {items && items.length ? (
        <div className="st-inline-actions">
          {items.map((row) => (
            <button key={row.key} type="button" className="es-btn es-btn-sm" onClick={() => void remove(row.key)} title="Remove: it asks again next time">
              {row.label} ×
            </button>
          ))}
        </div>
      ) : null}
      {error ? <div className="st-muted" role="alert">{error}</div> : null}
    </Row>
  );
}
