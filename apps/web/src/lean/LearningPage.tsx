import React, { useCallback, useEffect, useMemo, useState } from "react";
import { leanApi } from "./api";
import { PageShell } from "./Pages";
import { ShowMore, useShowMore } from "./ShowMore";
import type { LearningEpisode, LearningEvent, LearningLesson, LearningProfile, LearningStatus, LessonStatus, ReliabilityRow } from "./types";

/**
 * What agents learned (backend agent/learning): track records, lessons with
 * their evidence and history, the review queue, and tool reliability. Every
 * change here is the owner's and is logged, so any of it can be rolled back.
 */

const STATUS_LABEL: Record<LessonStatus, string> = {
  pending_review: "Waiting for you",
  probation: "Unproven",
  established: "Proven",
  retired: "Retired",
  quarantined: "Rejected",
};

// The verification ladder, in plain words.
const LEVEL_LABEL = ["Claimed", "Ran", "Checked", "Double-checked", "You confirmed"];
const OUTCOME_LABEL: Record<string, string> = { success: "Worked", failure: "Failed", stopped: "Stopped", answered: "Answered", error: "Model error" };
const ACTION_LABEL: Record<string, string> = {
  created: "Learned", approve: "You approved it", reject: "You rejected it", promote: "You marked it proven",
  retire: "You retired it", restore: "You restored it", edit: "You edited it", rollback: "Rolled back",
  deleted: "You deleted it", evidence: "Seen again", used: "Used",
};

const kindLabel = (kind: string) => (kind || "general").replace(/_/g, " ");
const sentenceCase = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

const ago = (seconds: number) => {
  if (!seconds) return "";
  const s = Math.max(0, Date.now() / 1000 - seconds);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(seconds * 1000).toLocaleDateString([], { month: "short", day: "numeric" });
};

function actionLabel(event: LearningEvent): string {
  if (event.action.startsWith("status:")) return `Became ${STATUS_LABEL[event.action.slice(7) as LessonStatus]?.toLowerCase() || event.action.slice(7)}`;
  if (event.action.startsWith("counted:")) return event.action === "counted:win" ? "Helped a checked task" : event.action === "counted:loss" ? "Used on a task that failed" : "Used";
  return ACTION_LABEL[event.action] || event.action;
}

type Data = {
  status: LearningStatus;
  profiles: LearningProfile[];
  lessons: LearningLesson[];
  episodes: LearningEpisode[];
  reliability: Record<string, ReliabilityRow[]>;
};

export function LearningPage({ apiBase }: { apiBase: string }) {
  const api = useMemo(() => leanApi(apiBase), [apiBase]);
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [agentFilter, setAgentFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<"active" | "retired" | "all">("active");
  const [openLesson, setOpenLesson] = useState("");

  const load = useCallback(async () => {
    try {
      const [status, profiles, lessons, episodes, reliability] = await Promise.all([
        api.learningStatus(), api.learningProfiles(), api.lessons(), api.episodes("", 30), api.reliability(),
      ]);
      setData({ status, profiles, lessons, episodes, reliability });
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't load what agents learned.");
    }
  }, [api]);

  useEffect(() => { void load(); }, [load]);

  const act = async (work: () => Promise<unknown>, done = "") => {
    setNotice("");
    try {
      await work();
      if (done) setNotice(done);
      await load();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "That didn't work.");
    }
  };

  const pending = (data?.lessons || []).filter((l) => l.status === "pending_review");
  const shown = (data?.lessons || []).filter((l) => {
    if (l.status === "pending_review") return false;
    if (agentFilter && l.agent_id !== agentFilter) return false;
    if (statusFilter === "active") return l.status === "probation" || l.status === "established";
    if (statusFilter === "retired") return l.status === "retired" || l.status === "quarantined";
    return true;
  }).sort((a, b) => (a.status === "established" ? 0 : 1) - (b.status === "established" ? 0 : 1) || b.updated_at - a.updated_at);
  const moreLessons = useShowMore(shown);
  const moreEpisodes = useShowMore(data?.episodes || []);
  const names = new Map((data?.profiles || []).map((p) => [p.agent_id, p.name]));

  return (
    <PageShell
      title="Learning"
      lead="Lessons your agents took from their own checked work. Advice only: never permissions, approvals or tools."
      action={<button type="button" className="es-btn" disabled={!data?.status.enabled || !data?.status.reflections_pending}
        title="Review finished tasks now instead of waiting for a quiet moment"
        onClick={() => void act(() => api.reflectNow(), "Reviewing finished tasks in the background. Refresh in a minute.")}>Review now</button>}
    >
      {error ? <div className="es-learn-callout is-error" role="alert">{error} <button type="button" className="es-btn es-btn-sm" onClick={() => void load()}>Try again</button></div> : null}
      {!data && !error ? <div className="es-sec-empty" role="status">Loading…</div> : null}
      {data ? <>
        {!data.status.enabled ? (
          <div className="es-learn-callout" role="note">
            {data.status.mode === "control"
              ? "Learning is in evaluation (control) mode: agents read unrelated notes instead of lessons, and nothing new is recorded."
              : "Learning is off. Agents don't record new experience or read lessons. Turn it on in Settings › General."}
          </div>
        ) : null}
        <div className="es-learn-stats" aria-live="polite">
          <Stat value={data.status.episodes} label={data.status.episodes === 1 ? "task graded" : "tasks graded"} />
          <Stat value={data.status.lessons.established || 0} label="proven lessons" tone="ok" />
          <Stat value={data.status.lessons.probation || 0} label="unproven" />
          <Stat value={pending.length} label="waiting for you" tone={pending.length ? "warn" : undefined} />
          <Stat value={`${data.status.reflections_today}/${data.status.reflection_daily_cap}`} label="reviews today"
            fill={data.status.reflection_daily_cap ? data.status.reflections_today / data.status.reflection_daily_cap : 0} />
        </div>
        {data.episodes.length ? (
          <div className="es-learn-charts">
            <OutcomeChart episodes={data.episodes} />
            <LadderChart episodes={data.episodes} />
          </div>
        ) : null}
        {notice ? <p className="es-learn-notice" role="status">{notice}</p> : null}

        {pending.length ? (
          <section className="es-learn-section" aria-labelledby="learn-review">
            <h2 id="learn-review">Waiting for your review</h2>
            <p className="es-learn-lead">No agent reads these until you approve them.</p>
            <div className="es-learn-list">
              {pending.map((lesson) => (
                <LessonRow key={lesson.id} lesson={lesson} agentName={names.get(lesson.agent_id) || lesson.agent_id}
                  open={openLesson === lesson.id} onToggle={() => setOpenLesson((v) => (v === lesson.id ? "" : lesson.id))}
                  api={api} act={act} />
              ))}
            </div>
          </section>
        ) : null}

        <section className="es-learn-section" aria-labelledby="learn-agents">
          <h2 id="learn-agents">Agents</h2>
          <div className="es-page-list">
            {data.profiles.map((profile) => <AgentRow key={profile.agent_id} profile={profile} onPause={(paused) => void act(() => api.pauseLearning(profile.agent_id, paused))} />)}
          </div>
        </section>

        <section className="es-learn-section" aria-labelledby="learn-lessons">
          <div className="es-learn-head">
            <h2 id="learn-lessons">Lessons</h2>
            <div className="es-learn-filters">
              <select aria-label="Agent" value={agentFilter} onChange={(e) => setAgentFilter(e.target.value)}>
                <option value="">All agents</option>
                {data.profiles.map((p) => <option key={p.agent_id} value={p.agent_id}>{p.name}</option>)}
              </select>
              <div className="es-learn-seg" role="group" aria-label="Show">
                {(["active", "retired", "all"] as const).map((value) => (
                  <button key={value} type="button" aria-pressed={statusFilter === value} onClick={() => setStatusFilter(value)}>
                    {value === "active" ? "In use" : value === "retired" ? "Retired" : "All"}
                  </button>
                ))}
              </div>
            </div>
          </div>
          {shown.length === 0 ? (
            <div className="es-page-empty is-static">
              <strong>{statusFilter === "active" ? "No lessons in use yet" : "Nothing here"}</strong>
              <span>After a task is checked (a file read back, a test run, a source opened) or you press Worked or Didn't work, the agent writes down what to repeat or avoid.</span>
            </div>
          ) : (
            <>
              <div className="es-learn-list">
                {moreLessons.shown.map((lesson) => (
                  <LessonRow key={lesson.id} lesson={lesson} agentName={names.get(lesson.agent_id) || lesson.agent_id}
                    open={openLesson === lesson.id} onToggle={() => setOpenLesson((v) => (v === lesson.id ? "" : lesson.id))}
                    api={api} act={act} />
                ))}
              </div>
              {moreLessons.collapsible ? <ShowMore expanded={moreLessons.expanded} hidden={moreLessons.hidden} label="lessons" onToggle={() => moreLessons.setExpanded((v) => !v)} /> : null}
            </>
          )}
        </section>

        <section className="es-learn-section" aria-labelledby="learn-episodes">
          <h2 id="learn-episodes">Recent tasks</h2>
          {data.episodes.length === 0 ? (
            <div className="es-sec-empty">No graded tasks yet.</div>
          ) : (
            <>
              <div className="es-learn-list">
                {moreEpisodes.shown.map((ep) => <EpisodeRow key={ep.id} episode={ep} />)}
              </div>
              {moreEpisodes.collapsible ? <ShowMore expanded={moreEpisodes.expanded} hidden={moreEpisodes.hidden} label="tasks" onToggle={() => moreEpisodes.setExpanded((v) => !v)} /> : null}
            </>
          )}
        </section>

        <section className="es-learn-section" aria-labelledby="learn-reliability">
          <h2 id="learn-reliability">Reliability</h2>
          <div className="es-learn-tables">
            <ReliabilityBars title="Tools" rows={data.reliability.tool || []} />
            <ReliabilityBars title="Search providers" rows={data.reliability.search_provider || []} />
          </div>
        </section>
      </> : null}
    </PageShell>
  );
}

/** One big number with a label; `fill` (0–1) draws a thin meter under it. */
function Stat({ value, label, tone, fill }: { value: number | string; label: string; tone?: "ok" | "warn"; fill?: number }) {
  return (
    <div className={`es-learn-stat${tone ? ` is-${tone}` : ""}`}>
      <strong>{value}</strong>
      <span>{label}</span>
      {fill !== undefined ? <i className="es-learn-meter" aria-hidden><b style={{ width: `${Math.round(Math.min(1, fill) * 100)}%` }} /></i> : null}
    </div>
  );
}

type Bucket = "worked" | "answered" | "failed";
const BUCKET_LABEL: Record<Bucket, string> = { worked: "Worked", answered: "Answered", failed: "Failed" };
const bucketOf = (ep: LearningEpisode): Bucket => ep.failed || ep.outcome === "error" || ep.feedback < 0 ? "failed" : ep.verified_success || ep.feedback > 0 ? "worked" : "answered";

/** Graded tasks per day for the last two weeks, stacked by how they went. */
function OutcomeChart({ episodes }: { episodes: LearningEpisode[] }) {
  const days = 14;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const start = today.getTime() / 1000 - (days - 1) * 86400;
  const cols = Array.from({ length: days }, (_, i) => ({ at: start + i * 86400, worked: 0, answered: 0, failed: 0 }));
  for (const ep of episodes) {
    const i = Math.floor((ep.created_at - start) / 86400);
    if (i >= 0 && i < days) cols[i][bucketOf(ep)] += 1;
  }
  const peak = Math.max(1, ...cols.map((c) => c.worked + c.answered + c.failed));
  const totals = { worked: 0, answered: 0, failed: 0 };
  for (const c of cols) { totals.worked += c.worked; totals.answered += c.answered; totals.failed += c.failed; }
  return (
    <figure className="es-learn-chart">
      <figcaption>
        <strong>Tasks, last 14 days</strong>
        <span className="es-learn-legend">
          {(Object.keys(BUCKET_LABEL) as Bucket[]).map((b) => <span key={b}><i className={`is-${b}`} />{BUCKET_LABEL[b]} {totals[b]}</span>)}
        </span>
      </figcaption>
      <div className="es-learn-cols" role="img" aria-label={`Tasks in the last 14 days: ${totals.worked} worked, ${totals.answered} answered, ${totals.failed} failed`}>
        {cols.map((c) => {
          const total = c.worked + c.answered + c.failed;
          const day = new Date(c.at * 1000).toLocaleDateString([], { month: "short", day: "numeric" });
          return (
            <div key={c.at} className="es-learn-col" title={total ? `${day}: ${c.worked} worked, ${c.answered} answered, ${c.failed} failed` : `${day}: no tasks`}>
              <div className="es-learn-stack" style={{ height: `${(total / peak) * 100}%` }}>
                {(["failed", "answered", "worked"] as Bucket[]).map((b) => c[b] ? <i key={b} className={`is-${b}`} style={{ flexGrow: c[b] }} /> : null)}
              </div>
            </div>
          );
        })}
      </div>
      <div className="es-learn-axis" aria-hidden>
        <span>{new Date(start * 1000).toLocaleDateString([], { month: "short", day: "numeric" })}</span>
        <span>Today</span>
      </div>
    </figure>
  );
}

/** How sure EchoSpeak is that tasks worked: one bar per rung of the ladder. */
function LadderChart({ episodes }: { episodes: LearningEpisode[] }) {
  const counts = LEVEL_LABEL.map(() => 0);
  for (const ep of episodes) counts[ep.feedback > 0 ? 4 : Math.max(0, Math.min(3, ep.level))] += 1;
  const peak = Math.max(1, ...counts);
  return (
    <figure className="es-learn-chart">
      <figcaption><strong>How sure it worked</strong><span className="es-learn-legend">{episodes.length} recent tasks</span></figcaption>
      <ul className="es-learn-hbars">
        {LEVEL_LABEL.map((label, i) => (
          <li key={label} title={`${label}: ${counts[i]}`}>
            <span>{label}</span>
            <span className="es-learn-hbar" aria-hidden><i style={{ width: `${(counts[i] / peak) * 100}%`, opacity: 0.45 + i * 0.14 }} /></span>
            <span>{counts[i]}</span>
          </li>
        ))}
      </ul>
    </figure>
  );
}

function AgentRow({ profile, onPause }: { profile: LearningProfile; onPause(paused: boolean): void }) {
  const kinds = Object.entries(profile.kinds).filter(([, row]) => row.decided > 0).sort((a, b) => b[1].decided - a[1].decided).slice(0, 4);
  const wins = kinds.reduce((n, [, row]) => n + row.wins, 0);
  const decided = kinds.reduce((n, [, row]) => n + row.decided, 0);
  return (
    <div className="es-page-row es-learn-agent">
      <span className="es-page-row-text">
        <strong>{profile.name}</strong>
        <small>{profile.title}{profile.false_success ? ` · claimed done ${plural(profile.false_success, "time")} when it wasn't` : ""}</small>
      </span>
      <span className="es-learn-agent-rate" title={decided ? `${wins} of ${decided} checked tasks worked` : "No checked tasks yet"}>
        <span className="es-learn-bar" aria-hidden><i style={{ width: `${decided ? Math.round((wins / decided) * 100) : 0}%` }} /></span>
        <small>{decided ? `${Math.round((wins / decided) * 100)}%` : "–"}</small>
      </span>
      <span className="es-learn-kind-chips">
        {kinds.map(([kind, row]) => <span key={kind} title={`${sentenceCase(kindLabel(kind))}: ${row.wins} of ${row.decided} worked`}>{sentenceCase(kindLabel(kind))} <b>{row.wins}/{row.decided}</b></span>)}
      </span>
      <small className="es-learn-agent-lessons">{profile.lessons_proven} proven · {profile.lessons_unproven} unproven</small>
      <button type="button" className="es-btn es-btn-sm es-btn-quiet" aria-pressed={!profile.paused}
        title={profile.paused ? "Let this agent learn and read lessons again" : "Stop this agent learning or reading lessons"}
        onClick={() => onPause(!profile.paused)}>{profile.paused ? "Paused" : "Learning"}</button>
    </div>
  );
}

function LessonRow({ lesson, agentName, open, onToggle, api, act }: {
  lesson: LearningLesson;
  agentName: string;
  open: boolean;
  onToggle(): void;
  api: ReturnType<typeof leanApi>;
  act(work: () => Promise<unknown>, done?: string): Promise<void>;
}) {
  const [detail, setDetail] = useState<{ events: LearningEvent[]; episodes: LearningEpisode[] } | null>(null);
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(lesson.title);
  const [editError, setEditError] = useState("");
  const saveEdit = async () => {
    setEditError("");
    try {
      await api.editLesson(lesson.id, { title, text });
      setEditing(false);
      await act(async () => undefined);
    } catch (err) {
      setEditError(err instanceof Error ? err.message : "Not saved.");
    }
  };
  const [text, setText] = useState(lesson.text);
  useEffect(() => {
    if (!open) return;
    let live = true;
    api.lesson(lesson.id).then((d) => live && setDetail({ events: d.events, episodes: d.episodes })).catch(() => live && setDetail({ events: [], episodes: [] }));
    return () => { live = false; };
  }, [open, api, lesson.id, lesson.updated_at]);

  const action = (name: "approve" | "reject" | "promote" | "retire" | "restore", label: string, primary = false) => (
    <button type="button" className={`es-btn es-btn-sm${primary ? " es-btn-primary" : ""}`} onClick={() => void act(() => api.lessonAction(lesson.id, name))}>{label}</button>
  );
  const decided = lesson.wins + lesson.losses;
  return (
    <article className={`es-learn-row is-${lesson.status}`}>
      <div className="es-learn-row-main">
        <div className="es-learn-tags">
          <span className={`es-learn-tag is-${lesson.status}`}>{STATUS_LABEL[lesson.status]}</span>
          <span className="es-learn-tag">{lesson.kind === "avoid" ? "Avoid" : "Do"}</span>
          <span className="es-learn-meta">{agentName} · {kindLabel(lesson.task_kind)}{lesson.edited ? " · edited by you" : ""}</span>
        </div>
        {editing ? (
          <div className="es-learn-edit">
            <label>Title<input value={title} maxLength={80} onChange={(e) => setTitle(e.target.value)} /></label>
            <label>Lesson<textarea value={text} maxLength={400} onChange={(e) => setText(e.target.value)} /></label>
            <small>Lessons can't be about permissions, approvals, safety rules, secrets, or skipping or changing checks.</small>
            {editError ? <p className="es-learn-error" role="alert">{sentenceCase(editError)}</p> : null}
            <div>
              <button type="button" className="es-btn es-btn-sm es-btn-quiet" onClick={() => { setEditing(false); setTitle(lesson.title); setText(lesson.text); }}>Cancel</button>
              <button type="button" className="es-btn es-btn-sm es-btn-primary" disabled={!title.trim() || !text.trim()}
                onClick={() => void saveEdit()}>Save</button>
            </div>
          </div>
        ) : (
          <>
            <strong>{lesson.title}</strong>
            <p>{lesson.text}</p>
          </>
        )}
        <small className="es-learn-why">
          {lesson.uses ? `Used ${lesson.uses}× · helped ${lesson.wins} · failed ${lesson.losses}${decided ? "" : " (no checked result yet)"}` : "Not used yet"}
          {lesson.note ? ` · ${lesson.note}` : ""}
        </small>
      </div>
      <div className="es-learn-actions">
        {lesson.status === "pending_review" ? <>{action("approve", "Approve", true)}{action("reject", "Reject")}</> : null}
        {lesson.status === "probation" ? action("promote", "Mark proven") : null}
        {lesson.status === "established" ? (
          <button type="button" className="es-btn es-btn-sm" title="Save this proven lesson as an Agent Skill (you approve it in Settings › Skills)"
            onClick={() => void act(() => api.skillFromLesson(lesson.id), "Made a draft skill. Review and approve it in Settings › Skills.")}>Make a skill</button>
        ) : null}
        {lesson.status === "probation" || lesson.status === "established" ? action("retire", "Retire") : null}
        {lesson.status === "retired" || lesson.status === "quarantined" ? action("restore", "Restore") : null}
        <button type="button" className="es-btn es-btn-sm es-btn-quiet" aria-expanded={open} onClick={onToggle}>{open ? "Hide" : "Details"}</button>
      </div>
      {open ? (
        <div className="es-learn-detail">
          <div className="es-learn-detail-actions">
            {!editing ? <button type="button" className="es-btn es-btn-sm" onClick={() => setEditing(true)}>Edit</button> : null}
            <button type="button" className="es-btn es-btn-sm es-btn-danger" onClick={() => {
              if (window.confirm(`Delete “${lesson.title}”? It stays in the history, so you can undo this.`)) void act(() => api.deleteLesson(lesson.id));
            }}>Delete</button>
          </div>
          <h3>Learned from</h3>
          {detail?.episodes.length ? (
            <ul className="es-learn-sources">
              {detail.episodes.map((ep) => (
                <li key={ep.id}>
                  <span>{ep.goal.slice(0, 140)}{ep.goal.length > 140 ? "…" : ""}</span>
                  <small>{OUTCOME_LABEL[ep.outcome] || ep.outcome} · {LEVEL_LABEL[ep.feedback > 0 ? 4 : ep.level]} · {ago(ep.created_at)}{ep.trusted ? "" : " · read outside content"}</small>
                </li>
              ))}
            </ul>
          ) : <small>{detail ? "Those tasks are no longer kept." : "Loading…"}</small>}
          <h3>History</h3>
          {detail?.events.length ? (
            <ol className="es-learn-history">
              {detail.events.slice().reverse().map((event) => (
                <li key={event.id}>
                  <span>{actionLabel(event)}{event.reason ? <small> · {event.reason}</small> : null}</span>
                  <time>{ago(event.at)}</time>
                  <button type="button" className="es-btn es-btn-sm es-btn-quiet"
                    title={event.action === "created" ? "Retire it, as if it was never learned" : "Put the lesson back the way it was before this"}
                    onClick={() => void act(() => api.rollbackLesson(lesson.id, event.id), "Rolled back.")}>Undo</button>
                </li>
              ))}
            </ol>
          ) : <small>{detail ? "No history." : "Loading…"}</small>}
        </div>
      ) : null}
    </article>
  );
}

function EpisodeRow({ episode }: { episode: LearningEpisode }) {
  const [open, setOpen] = useState(false);
  const level = episode.feedback > 0 ? 4 : episode.level;
  return (
    <article className="es-learn-row es-learn-episode">
      <button type="button" className="es-learn-episode-main" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <span className={`es-learn-tag is-${episode.failed ? "failed" : episode.verified_success ? "verified" : "neutral"}`}>{OUTCOME_LABEL[episode.outcome] || episode.outcome}</span>
        <span className="es-learn-level" title="How sure EchoSpeak is that it worked">{LEVEL_LABEL[level]}</span>
        <span className="es-learn-goal">{episode.goal || "(no request text)"}</span>
        <small>{episode.agent_name || episode.agent_id} · {kindLabel(episode.task_kind)} · {ago(episode.created_at)}{episode.feedback ? (episode.feedback > 0 ? " · you: worked" : " · you: didn't work") : ""}</small>
      </button>
      {open ? (
        <div className="es-learn-detail">
          <ul className="es-learn-reasons">
            {episode.reasons.map((reason, i) => <li key={i}>{reason}</li>)}
            {episode.summary ? <li>{episode.summary}</li> : null}
            {!episode.trusted ? <li>Read outside content ({episode.taint.join(", ")}): anything learned waits for your review.</li> : null}
            {episode.feedback_note ? <li>Your note: “{episode.feedback_note}”</li> : null}
          </ul>
          {episode.tools.length ? (
            <ol className="es-learn-tools">
              {episode.tools.slice(-12).map((tool, i) => (
                <li key={i} className={tool.not_run ? "is-skipped" : tool.ok ? "is-ok" : "is-failed"}>
                  <span>{tool.label || tool.name}</span>
                  <small>{tool.not_run ? "not run" : tool.ok ? "ok" : "failed"}</small>
                </li>
              ))}
            </ol>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

function ReliabilityBars({ title, rows }: { title: string; rows: ReliabilityRow[] }) {
  const sorted = rows.slice().sort((a, b) => (b.recent_ok + b.recent_failed) - (a.recent_ok + a.recent_failed));
  return (
    <figure className="es-learn-chart">
      <figcaption><strong>{title}</strong><span className="es-learn-legend"><span><i className="is-worked" />Worked</span><span><i className="is-failed" />Failed</span></span></figcaption>
      {sorted.length === 0 ? <small className="es-learn-none">Nothing recorded yet.</small> : (
        <ul className="es-learn-hbars is-split">
          {sorted.slice(0, 10).map((row) => {
            const recent = row.recent_ok + row.recent_failed;
            const failing = recent >= 3 && row.recent_failed / recent >= 0.6;
            return (
              <li key={row.name} className={failing ? "is-failing" : undefined} title={`Lately ${row.recent_ok} of ${recent} worked · all time ${row.ok} ok, ${row.failed} failed`}>
                <span>{sentenceCase(row.name.replace(/_/g, " "))}</span>
                <span className="es-learn-hbar" aria-hidden>
                  <i className="is-worked" style={{ flexGrow: row.recent_ok }} />
                  <i className="is-failed" style={{ flexGrow: row.recent_failed }} />
                </span>
                <span>{recent ? `${row.recent_ok}/${recent}` : "–"}</span>
              </li>
            );
          })}
        </ul>
      )}
    </figure>
  );
}
