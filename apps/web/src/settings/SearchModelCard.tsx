import React, { useCallback, useEffect, useState } from "react";
import { ChoiceCards, Group, Row, Select, Status, Toggle } from "./controls";

// Settings › Memory & documents › Search by meaning (api/routes/memory.py /embeddings/search-model).
// One place for how Echo finds memories, documents and tools by meaning; replaces the old
// provider select, model text field and separate installer (docs/research/embeddings-2026-10-22.md).

export type SearchChoice = "off" | "fast" | "better" | "lmstudio" | "openai";

export type SearchStatus = {
  selected: SearchChoice;
  runtime_available: boolean;
  models: { key: string; label: string; size_mb: number; languages: string; installed: boolean }[];
  reranker: { label: string; size_mb: number; installed: boolean; enabled: boolean };
  lmstudio_models: { id: string; loaded: boolean }[];
  lmstudio_model: string;
  openai_ready: boolean;
  active: { available?: boolean; provider?: string; model?: string; detail?: string; label?: string };
};

/** Megabytes the switch will download: a local model that isn't there yet, and the reranker if newly wanted. */
export function downloadSize(status: SearchStatus, choice: SearchChoice, rerank: boolean): number {
  const model = status.models.find((m) => m.key === choice);
  let total = model && !model.installed ? model.size_mb : 0;
  if (rerank && !status.reranker.installed) total += status.reranker.size_mb;
  return total;
}

/** Whether the pending choice differs from what's saved. */
export function changed(status: SearchStatus, choice: SearchChoice, lmModel: string, rerank: boolean): boolean {
  if (choice !== status.selected || rerank !== status.reranker.enabled) return true;
  return choice === "lmstudio" && lmModel !== status.lmstudio_model;
}

export function buttonLabel(status: SearchStatus, choice: SearchChoice, rerank: boolean, busy: boolean): string {
  const size = downloadSize(status, choice, rerank);
  if (busy) return size ? "Downloading and re-indexing…" : "Re-indexing…";
  return size ? `Download (${size} MB) and switch` : "Switch";
}

/** The status line: what search by meaning is actually using right now. */
export function activeLine(status: SearchStatus): { tone: "ok" | "warn" | "idle"; text: string } {
  const active = status.active || {};
  if (status.selected === "off") return { tone: "idle", text: "Off: keyword search only" };
  if (active.available) {
    const name = active.label || (active.provider === "lm_studio" ? `LM Studio · ${active.model}` : active.provider === "openai" ? `OpenAI · ${active.model}` : active.model);
    return { tone: "ok", text: `Using ${name}` };
  }
  return { tone: "warn", text: active.detail ? `Not available: ${active.detail}. Keyword search is used.` : "Not set up yet: keyword search is used." };
}

async function request(url: string, init?: RequestInit): Promise<any> {
  const response = await fetch(url, init);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(String(data?.detail || `Request failed (${response.status})`));
  return data;
}

function firstLmModel(status: SearchStatus | null | undefined): string {
  if (!status) return "";
  return status.lmstudio_model || status.lmstudio_models.find((m) => m.loaded)?.id || status.lmstudio_models[0]?.id || "";
}

/** ``initial`` shows a known status straight away (tests; the card fetches its own otherwise). */
export function SearchModelCard({ apiBase, initial }: { apiBase: string; initial?: SearchStatus }) {
  const [status, setStatus] = useState<SearchStatus | null>(initial ?? null);
  const [choice, setChoice] = useState<SearchChoice>(initial?.selected ?? "fast");
  const [lmModel, setLmModel] = useState(firstLmModel(initial));
  const [rerank, setRerank] = useState(Boolean(initial?.reranker?.enabled));
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");

  const adopt = (data: SearchStatus) => {
    setStatus(data);
    setChoice(data.selected);
    setRerank(Boolean(data.reranker?.enabled));
    setLmModel(firstLmModel(data));
  };

  const load = useCallback(async () => {
    try {
      adopt(await request(`${apiBase}/embeddings/search-model`));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [apiBase]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!status) {
    return (
      <Group title="Search by meaning" description="How Echo finds memories, documents and tools by meaning.">
        <Row label={error ? <span className="st-muted">{error}</span> : "Loading…"} />
      </Group>
    );
  }

  const model = (key: string) => status.models.find((m) => m.key === key);
  const size = (key: string) => (model(key)?.installed ? "downloaded" : `${model(key)?.size_mb ?? "?"} MB`);
  const options = [
    { value: "off", title: "Keywords only", body: "Finds exact words. Nothing to download." },
    { value: "fast", title: "Fast · English", body: `all-MiniLM-L6-v2 on this PC · ${size("fast")}` },
    { value: "better", title: "Better · 100+ languages, longer text", badge: "Recommended",
      body: `EmbeddingGemma-300M on this PC · ${size("better")}` },
    { value: "lmstudio", title: "Use LM Studio",
      body: status.lmstudio_models.length
        ? "An embedding model loaded in LM Studio. Qwen3-Embedding-0.6B gives the best results."
        : "Load an embedding model in LM Studio first, for example Qwen3-Embedding-0.6B." },
    ...(status.openai_ready ? [{ value: "openai", title: "Cloud · OpenAI", body: "text-embedding-3-small. Your memories are sent to OpenAI; not used in Private or Offline mode." }] : []),
  ];
  const line = activeLine(status);
  const localNeeded = (choice === "fast" || choice === "better" || rerank) && !status.runtime_available;
  const canSwitch = changed(status, choice, lmModel, rerank) && !busy && !localNeeded && (choice !== "lmstudio" || Boolean(lmModel));

  const apply = async () => {
    setBusy(true);
    setError("");
    setNote("");
    try {
      const data = await request(`${apiBase}/embeddings/search-model`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ choice, model: choice === "lmstudio" ? lmModel : "", rerank }),
      });
      adopt(data);
      const done = data.reindexed || {};
      setNote(data.note || `Done. Re-indexed ${done.memories ?? 0} memories${done.documents ? ` and ${done.documents} documents` : ""}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Group title="Search by meaning" description="How Echo finds memories, documents and tools by meaning, not just matching words. The options on this PC never send your data anywhere.">
      <Row label={<Status tone={line.tone}>{line.text}</Status>} />
      <ChoiceCards value={choice} options={options} onChange={(v) => setChoice(v as SearchChoice)} />
      {choice === "lmstudio" ? (
        <Row label="LM Studio model" help={status.lmstudio_models.length ? "Models marked (loaded) are ready now." : "No embedding models found. Is LM Studio running?"}>
          {status.lmstudio_models.length ? (
            <Select value={lmModel} onChange={setLmModel}
              options={status.lmstudio_models.map((m) => ({ value: m.id, label: `${m.id}${m.loaded ? " (loaded)" : ""}` }))} />
          ) : (
            <button type="button" className="es-btn es-btn-sm es-btn-quiet" onClick={() => void load()}>Check again</button>
          )}
        </Row>
      ) : null}
      <Row label="Sharper results for English documents"
        help={`Re-orders the best document matches with a small model on this PC (${status.reranker.installed ? "downloaded" : `${status.reranker.size_mb} MB`}). It reads English, so leave it off for documents in other languages.`}>
        <Toggle checked={rerank} onChange={setRerank} label="Sharper document results" />
      </Row>
      <Row label={error ? <span className="st-muted">{error}</span> : note ? <span className="st-muted">{note}</span> : null}
        help={localNeeded ? "The local search runtime isn't installed in this build." : "Switching re-indexes your memories and documents on this PC; it can take a minute."}>
        <button type="button" className="es-btn es-btn-sm" disabled={!canSwitch} onClick={() => void apply()}>
          {buttonLabel(status, choice, rerank, busy)}
        </button>
      </Row>
    </Group>
  );
}
