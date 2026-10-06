import { useEffect, useMemo, useRef, useState, type MutableRefObject } from "react";
import { fetchWithTimeout, cloudProviders, isLmStudioOnlyLocked, listableProviders } from "../app/runtime";
import type { ProviderInfo, ProviderModelsResponse } from "../app/types";

const PROVIDER_LABELS: Record<string, string> = { lmstudio: "LM Studio", ollama: "Ollama", localai: "LocalAI", vllm: "vLLM" };

/** The model the active Session uses: provider info, the composer's provider/model picker,
 *  switching, and backend reachability (with slow retries while it is offline). */
export function useProviderSettings({
  apiBase,
  activeThreadIdRef,
  cancelSessionTurn,
}: {
  apiBase: string;
  activeThreadIdRef: MutableRefObject<string>;
  cancelSessionTurn: (sessionId: string, preserveStream?: boolean) => void;
}) {
  const backendRetryRef = useRef<{ attempt: number; timer: number | null }>({ attempt: 0, timer: null });

  const [providerInfo, setProviderInfo] = useState<ProviderInfo | null>(null);
  const [providerModels, setProviderModels] = useState<string[]>([]);
  const [providerDraft, setProviderDraft] = useState<{ provider: string; model: string; base_url: string }>({
    provider: "",
    model: "",
    base_url: "",
  });
  const lmStudioOnly = useMemo(() => isLmStudioOnlyLocked(providerInfo), [providerInfo]);
  const [providerError, setProviderError] = useState<string | null>(null);
  const [switchingProvider, setSwitchingProvider] = useState(false);
  // The picker says "Loading models…" instead of showing an empty list while a catalog loads.
  const [modelsLoading, setModelsLoading] = useState(false);
  const [backendOnline, setBackendOnline] = useState<boolean | null>(null);

  const lastAppliedProviderRef = useRef<{ provider: string; model: string } | null>(null);
  const suppressAutoApplyRef = useRef(true);

  const scheduleBackendRetry = () => {
    if (backendRetryRef.current.timer != null) return;
    const attempt = backendRetryRef.current.attempt;
    // Backoff is deliberately slow after the initial recovery window. The old
    // six-second ceiling could hammer a failing provider endpoint indefinitely
    // and turn one backend exception into a noisy desktop-wide failure loop.
    const delay = Math.min(30000, Math.round(900 * Math.pow(1.8, attempt)));
    backendRetryRef.current.attempt = Math.min(attempt + 1, 8);
    backendRetryRef.current.timer = window.setTimeout(() => {
      backendRetryRef.current.timer = null;
      refreshProviderInfo({ allowRetry: true });
    }, delay);
  };

  const refreshProviderInfo = async (opts: { allowRetry?: boolean } = {}) => {
    try {
      setProviderError(null);
      const scope = new URLSearchParams({ session_id: String(activeThreadIdRef.current || "default") });
      const resp = await fetchWithTimeout(`${apiBase}/provider?${scope.toString()}`, undefined, 10000);
      if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
      const info = (await resp.json()) as ProviderInfo;
      setProviderInfo(info);
      setBackendOnline(true);
      backendRetryRef.current.attempt = 0;
      if (backendRetryRef.current.timer != null) {
        window.clearTimeout(backendRetryRef.current.timer);
        backendRetryRef.current.timer = null;
      }
      lastAppliedProviderRef.current = { provider: info.provider, model: info.model };
      suppressAutoApplyRef.current = false;
      setProviderDraft((d) => ({
        ...d,
        provider: info.provider,
        model: info.model,
        base_url: info.base_url ? String(info.base_url) : d.base_url,
      }));
    } catch (e) {
      setBackendOnline(false);
      const err = e instanceof Error ? e : new Error(String(e));
      const msg = err.message || String(e);
      const aborted = err.name === "AbortError" || msg.toLowerCase().includes("aborted");
      const offline = aborted || msg.includes("Failed to fetch");
      const serverFailure = /\b5\d\d\b/.test(msg);
      const pretty = offline ? "Backend offline" : msg;
      const shouldRetry = Boolean(opts.allowRetry && (offline || serverFailure));
      setProviderError(offline && shouldRetry ? "Backend offline — retrying" : pretty);
      if (shouldRetry) scheduleBackendRetry();
    }
  };

  const modelsRequestRef = useRef(0);
  const modelsProviderRef = useRef("");
  const savedModelRef = useRef<{ provider: string; model: string }>({ provider: "", model: "" });
  const refreshProviderModels = async (provider: string) => {
    const request = ++modelsRequestRef.current;
    setModelsLoading(true);
    try {
      const resp = await fetchWithTimeout(`${apiBase}/provider/models?provider=${encodeURIComponent(provider)}`, undefined, 30000);
      if (!resp.ok) throw new Error(`Could not load models (HTTP ${resp.status})`);
      const data = (await resp.json()) as ProviderModelsResponse;
      if (modelsRequestRef.current !== request) return;
      const models = Array.isArray(data.models) ? data.models : [];
      modelsProviderRef.current = provider;
      savedModelRef.current = { provider, model: String(data.saved_model || "") };
      setProviderModels(models);
      if (cloudProviders.includes(provider)) setProviderError(data.reachable ? null : data.message || "Save an API key in Settings → Models, then refresh.");
      if (!models.length && listableProviders.includes(provider)) {
        const name = PROVIDER_LABELS[provider] || provider;
        setProviderError(`${name} isn't answering. Open ${name}, load a model and start its local server, then pick it again.`);
      } else {
        setProviderError((prev) => (prev && prev.includes("isn't answering") ? null : prev));
      }
    } catch (err) {
      if (modelsRequestRef.current === request) { setProviderModels([]); setProviderError(err instanceof Error ? err.message : String(err)); }
    } finally {
      if (modelsRequestRef.current === request) setModelsLoading(false);
    }
  };

  const applyProviderSwitch = async (draft?: { provider: string; model: string; base_url: string }) => {
    if (lmStudioOnly) return;
    const next = draft || providerDraft;
    if (!next.provider) return;
    setSwitchingProvider(true);
    setProviderError(null);
    cancelSessionTurn(String(activeThreadIdRef.current || ""));
    try {
      const body: any = {
        provider: next.provider,
        session_id: String(activeThreadIdRef.current || "default"),
        expected_revision: Number(providerInfo?.binding_revision || 1),
      };
      body.model = next.model || undefined;

      const resp = await fetchWithTimeout(`${apiBase}/provider/switch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        const t = await resp.text();
        throw new Error(t || `${resp.status} ${resp.statusText}`);
      }
      lastAppliedProviderRef.current = { provider: next.provider, model: next.model || "" };
      await refreshProviderInfo();
    } catch (e) {
      setProviderError(e instanceof Error ? e.message : String(e));
    } finally {
      setSwitchingProvider(false);
    }
  };

  useEffect(() => {
    return () => {
      if (backendRetryRef.current.timer != null) {
        window.clearTimeout(backendRetryRef.current.timer);
        backendRetryRef.current.timer = null;
      }
    };
  }, []);

  useEffect(() => {
    if (backendOnline === false) return;
    if ([...cloudProviders, ...listableProviders].includes(providerDraft.provider)) {
      setProviderModels([]);
      void refreshProviderModels(providerDraft.provider);
      return;
    }
    setProviderModels([]);
  }, [providerDraft.provider, backendOnline]);

  useEffect(() => {
    // Switching providers restores that provider's saved model (kept even when the catalog
    // omits it or can't be reached); only with nothing saved does the first catalog model apply.
    if (modelsProviderRef.current !== providerDraft.provider || providerDraft.model || modelsLoading) return;
    const saved = savedModelRef.current.provider === providerDraft.provider ? savedModelRef.current.model : "";
    const pick = saved || providerModels[0] || "";
    if (pick) setProviderDraft((d) => ({ ...d, model: pick }));
  }, [providerModels, providerDraft.provider, providerDraft.model, modelsLoading]);

  useEffect(() => {
    if (lmStudioOnly) return;
    if (suppressAutoApplyRef.current) return;
    if (switchingProvider) return;

    const next = { provider: providerDraft.provider, model: providerDraft.model, base_url: providerDraft.base_url };
    if ([...cloudProviders, ...listableProviders].includes(next.provider) && !next.model) return;
    const last = lastAppliedProviderRef.current;
    if (last && last.provider === next.provider && last.model === (next.model || "")) return;

    const t = window.setTimeout(() => {
      applyProviderSwitch(next);
    }, next.provider === "llama_cpp" ? 800 : 250);

    return () => window.clearTimeout(t);
  }, [providerDraft.provider, providerDraft.model, providerDraft.base_url, switchingProvider]);

  useEffect(() => {
    const onSettingsSaved = () => {
      void refreshProviderInfo();
      if (providerDraft.provider) void refreshProviderModels(providerDraft.provider);
    };
    window.addEventListener("echospeak:settings-saved", onSettingsSaved);
    return () => window.removeEventListener("echospeak:settings-saved", onSettingsSaved);
  }, [apiBase, providerDraft.provider]);

  const showModelPicker = cloudProviders.includes(providerDraft.provider) || providerModels.length > 0;
  const modelPickerOptions = [...new Set([providerDraft.model, ...providerModels].filter(Boolean))];
  const modelPickerValue = showModelPicker ? providerDraft.model : modelPickerOptions[0];

  return {
    providerInfo, providerModels, setProviderModels, providerDraft, setProviderDraft, providerError, setProviderError,
    switchingProvider, backendOnline, setBackendOnline, lmStudioOnly,
    refreshProviderInfo, applyProviderSwitch,
    showModelPicker, modelPickerOptions, modelPickerValue, modelsLoading,
  };
}
