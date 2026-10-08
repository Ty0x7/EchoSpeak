import React, { useCallback, useEffect, useMemo, useState } from "react";
import { leanApi, type AgentSkillInfo } from "../lean/api";
import "./agentskills.css";

/**
 * Settings › Skills: Agent Skills in the open SKILL.md format
 * (backend: agent/lean/agent_skills.py). Agents only see a skill after you
 * approve it here, and any later change to it needs approving again.
 */

const STATUS: Record<AgentSkillInfo["status"], string> = {
  approved: "Approved",
  needs_review: "Needs review",
  changed: "Changed since you approved it",
  invalid: "Can't be used",
};

export function AgentSkillsSection({ apiBase }: { apiBase: string }) {
  const api = useMemo(() => leanApi(apiBase), [apiBase]);
  const [skills, setSkills] = useState<AgentSkillInfo[] | null>(null);
  const [open, setOpen] = useState("");
  const [notice, setNotice] = useState("");
  const [mode, setMode] = useState<"folder" | "paste">("folder");
  const [path, setPath] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setSkills(await api.agentSkills());
    } catch (err) {
      setNotice(err instanceof Error ? err.message : String(err));
    }
  }, [api]);
  useEffect(() => { void load(); }, [load]);

  const run = async (work: () => Promise<unknown>, done: string) => {
    setBusy(true);
    setNotice("");
    try {
      await work();
      setNotice(done);
      await load();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "That didn't work.");
    } finally {
      setBusy(false);
    }
  };

  const importSkill = () => run(async () => {
    const skill = await api.importAgentSkill(mode === "folder" ? { path: path.trim() } : { text });
    setOpen(skill.name);
    setPath("");
    setText("");
  }, "Imported. Review it below, then approve it so agents can use it.");

  const waiting = (skills || []).filter((s) => s.status === "needs_review" || s.status === "changed").length;
  return (
    <div className="st-section">
      <p className="st-skills-intro">
        Skills are written instructions for specific kinds of tasks, in the open SKILL.md format other agents use too.
        Agents see a skill's name and description, and read the rest only when a task needs it. Nothing is used until you approve it.
      </p>
      {notice ? <p className="st-skills-notice" role="status">{notice}</p> : null}

      <div className="st-skills-list">
        {skills === null ? <p className="st-empty">Loading…</p> : null}
        {skills && !skills.length ? <p className="st-empty">No skills yet. Import one below, or turn a proven lesson into one from Learning.</p> : null}
        {waiting ? <p className="st-skills-waiting">{waiting} skill{waiting === 1 ? "" : "s"} waiting for your review.</p> : null}
        {(skills || []).map((skill) => (
          <div className="st-skill" key={skill.name} data-status={skill.status}>
            <div className="st-skill-head">
              <div>
                <b>{skill.name}</b>
                <span className="st-skill-status">{STATUS[skill.status] || skill.status}</span>
                <p>{skill.problem || skill.description}</p>
              </div>
              <button type="button" className="es-btn es-btn-sm" aria-expanded={open === skill.name} onClick={() => setOpen(open === skill.name ? "" : skill.name)}>
                {open === skill.name ? "Close" : skill.status === "approved" ? "View" : "Review"}
              </button>
            </div>
            {open === skill.name ? (
              <div className="st-skill-body">
                <pre aria-label="The skill's instructions">{skill.body || "(no instructions)"}</pre>
                {skill.files.length ? <p className="st-skill-files">Files: {skill.files.join(", ")}</p> : null}
                <div className="st-skill-actions">
                  {skill.status === "needs_review" || skill.status === "changed" ? (
                    <button type="button" className="es-btn es-btn-sm es-btn-primary" disabled={busy}
                      onClick={() => void run(() => api.approveAgentSkill(skill.name, skill.digest), `Approved ${skill.name}. Agents can use it now.`)}>
                      Approve
                    </button>
                  ) : null}
                  <button type="button" className="es-btn es-btn-sm es-btn-danger" disabled={busy}
                    onClick={() => void run(() => api.removeAgentSkill(skill.name), `Removed ${skill.name}.`)}>
                    Remove
                  </button>
                </div>
              </div>
            ) : null}
          </div>
        ))}
      </div>

      <div className="st-skills-import">
        <div className="st-skills-tabs" role="group" aria-label="Import from">
          <button type="button" aria-pressed={mode === "folder"} className={mode === "folder" ? "is-on" : ""} onClick={() => setMode("folder")}>From a folder</button>
          <button type="button" aria-pressed={mode === "paste"} className={mode === "paste" ? "is-on" : ""} onClick={() => setMode("paste")}>Paste SKILL.md</button>
        </div>
        {mode === "folder" ? (
          <input id="skill-folder" value={path} onChange={(e) => setPath(e.target.value)} placeholder="C:\path\to\my-skill (the folder with SKILL.md)" aria-label="Skill folder" />
        ) : (
          <textarea id="skill-text" value={text} onChange={(e) => setText(e.target.value)} rows={8} aria-label="SKILL.md contents"
            placeholder={"---\nname: my-skill\ndescription: When to use it.\n---\n\nThe instructions."} />
        )}
        <button type="button" className="es-btn es-btn-primary" disabled={busy || (mode === "folder" ? !path.trim() : !text.trim())} onClick={() => void importSkill()}>
          {busy ? "Working…" : "Import"}
        </button>
      </div>
    </div>
  );
}
