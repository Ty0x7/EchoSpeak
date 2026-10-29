import React, { useCallback, useEffect, useMemo, useState } from "react";
import { leanApi } from "./api";
import { PageShell } from "./Pages";
import type { LearningEpisode, LearningEvent, LearningLesson, LearningProfile, LearningStatus, LessonStatus, ReliabilityRow } from "./types";

/**
 * Learning (backend agent/learning), for everyday use: what Echo learned, whether it's
 * helping, what happened recently and what needs you, three items at a time. Every
 * record stays available through View all and its detail view, where the actions live.
 * Every change is the owner's and is logged, so any of it can be undone.
 */

type Api = ReturnType<typeof leanApi>;
type Act = (work: () => Promise<unknown>, done?: string) => Promise<void>;
type View =
  | { kind: "overview" }
  | { kind: "lessons" }
  | { kind: "experiences" }
  | { kind: "review" }
  | { kind: "guide" }
  | { kind: "lesson"; id: string }
  | { kind: "experience"; episode: LearningEpisode };

type Data = {
  status: LearningStatus;
  profiles: LearningProfile[];
  lessons: LearningLesson[];
  recent: LearningEpisode[];
};

const PREVIEW = 3;
const PAGE = 20;

// ── plain words ──────────────────────────────────────────────────────────

const ago = (seconds: number) => {
  if (!seconds) return "";
  const s = Math.max(0, Date.now() / 1000 - seconds);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(seconds * 1000).toLocaleDateString([], { month: "short", day: "numeric" });
};
const when = (seconds: number) =>
  seconds ? new Date(seconds * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "";
const kindLabel = (kind: string) => {
  const text = (kind || "general").replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
};
const sentence = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

const LESSON_BADGE: Record<LessonStatus, string> = {
  pending_review: "Needs review",
  probation: "Being tested",
  established: "Proven",
  retired: "Retired",
  quarantined: "Rejected",
};

/** What the control comparison says about a lesson, honestly. */
function evidenceOf(lesson: LearningLesson) {
  const decided = lesson.wins + lesson.losses;
  const heldWins = lesson.held_wins ?? 0;
  const held = heldWins + (lesson.held_losses ?? 0);
  const withRate = decided ? lesson.wins / decided : 0;
  const withoutRate = held ? heldWins / held : 0;
  const compared = held >= 3 && decided >= 1;
  let verdict: "better" | "same" | "worse" | "unknown" = "unknown";
  if (compared) verdict = withRate > withoutRate + 0.05 ? "better" : withRate + 0.25 < withoutRate ? "worse" : "same";
  return { decided, held, heldWins, withRate, withoutRate, compared, verdict };
}

function lessonLine(lesson: LearningLesson): string {
  const e = evidenceOf(lesson);
  if (lesson.status === "pending_review") return "Learned while reading outside content or from an unusual task, so Echo won't use it until you decide.";
  if (lesson.status === "retired") return lesson.note ? sentence(lesson.note) : "No longer used.";
  if (lesson.status === "quarantined") return "You rejected it. Echo never uses it.";
  if (!lesson.uses) return "New: Echo hasn't used it on a task yet.";
  if (e.compared) {
    if (e.verdict === "better") return `Helps: ${lesson.wins} of ${e.decided} checked tasks worked with it, ${e.heldWins} of ${e.held} without it.`;
    if (e.verdict === "worse") return `Tasks went better without it (${e.heldWins} of ${e.held} vs ${lesson.wins} of ${e.decided}).`;
    return `No clear difference yet: ${lesson.wins} of ${e.decided} with it, ${e.heldWins} of ${e.held} without.`;
  }
  return e.decided
    ? `Used ${plural(lesson.uses, "time")}; ${lesson.wins} of ${e.decided} checked tasks worked. Not enough evidence yet to say it helps.`
    : `Used ${plural(lesson.uses, "time")}, but none of those tasks could be checked yet.`;
}

/** How an experience went, in the words a person would use. */
function outcomeOf(ep: LearningEpisode): { label: string; tone: "ok" | "warn" | "bad" | "neutral" } {
  if (ep.feedback > 0) return { label: "You said it worked", tone: "ok" };
  if (ep.feedback < 0) return { label: "You said it didn't work", tone: "bad" };
  if (ep.outcome === "error") return { label: "The model failed", tone: "neutral" };
  const blocked = BLOCKED[ep.effective_cause || ""];
  if (blocked) return { label: blocked, tone: "neutral" };
  if (ep.effective_cause === "false_success") return { label: "Said done, but the checks disagreed", tone: "bad" };
  if (ep.failed) return { label: ep.outcome === "stopped" ? "Stopped before finishing" : "Didn't work", tone: "bad" };
  if (ep.verified_success) return { label: "Checked and worked", tone: "ok" };
  return { label: "Done, but not checked", tone: "warn" };
}

const isWorker = (ep: LearningEpisode) => ep.role === "worker" && ep.source === "swarm";
const whoOf = (ep: LearningEpisode) => `${ep.agent_name || ep.agent_id}${isWorker(ep) ? " · research worker" : ep.role === "worker" ? " · helping a teammate" : ""}`;

/** Unchecked replies where your Worked / Didn't work is the only evidence there will be. */
const wantsFeedback = (ep: LearningEpisode) =>
  !isWorker(ep) && !ep.feedback && Boolean(ep.execution_id) && ep.outcome !== "error" && !ep.failed && !ep.verified_success;

/** Causes that weren't the agent's strategy at work: never counted for or against a lesson. */
const BLOCKED: Record<string, string> = {
  environment: "Blocked by the setup",
  authorization: "Needed your permission",
  tool_failure: "A tool broke",
  information_gap: "Couldn't find what it needed",
  misunderstood: "You said it misunderstood",
};

const VERIFIED_TEXT: Record<string, string> = {
  verified: "Verified against the task's own acceptance check.",
  checks_pass: "The checks after the last change pass.",
  partial: "Partly verified.",
  failing: "The last check failed.",
  unverified: "Not verified.",
  blocked: "Blocked before it could be verified.",
};

const LEVEL_TEXT = [
  "Echo said it was done, but nothing it ran shows that.",
  "Tools ran and worked, but nothing checked the result afterwards.",
  "A check ran after the work: a file read back, a command or test run, or a source opened.",
  "Two different kinds of check agreed.",
  "You confirmed it worked.",
];

// ── the page ─────────────────────────────────────────────────────────────

export function LearningPage({ apiBase }: { apiBase: string }) {
  const api = useMemo(() => leanApi(apiBase), [apiBase]);
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [view, setView] = useState<View>({ kind: "overview" });

  const load = useCallback(async () => {
    try {
      const [status, profiles, lessons, recent] = await Promise.all([
        api.learningStatus(), api.learningProfiles(), api.lessons(), api.episodesPage({ limit: 40 }),
      ]);
      setData({ status, profiles, lessons, recent: recent.episodes });
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't load what Echo learned.");
    }
  }, [api]);
  useEffect(() => { void load(); }, [load]);

  const act: Act = async (work, done = "") => {
    setNotice("");
    try {
      await work();
      if (done) setNotice(done);
      await load();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "That didn't work.");
    }
  };
  const go = (next: View) => {
    setNotice("");
    setView(next);
    window.requestAnimationFrame(() => document.querySelector(".es-lx")?.scrollIntoView({ block: "start" }));
  };
  const back = () => go({ kind: "overview" });
  const names = new Map((data?.profiles || []).map((p) => [p.agent_id, p.name]));
  const lessonById = new Map((data?.lessons || []).map((l) => [l.id, l]));

  return (
    <PageShell
      title="Learning diagnostics"
      lead="Echo learns on its own from checked work in Chat, Agents and Code; you don't need to manage it. This shows what it learned and whether it's helping. Lessons are advice only; they never change permissions, tools or settings."
      action={<button type="button" className="es-btn" aria-pressed={view.kind === "guide"} onClick={() => go(view.kind === "guide" ? { kind: "overview" } : { kind: "guide" })}>How learning works</button>}
    >
      <div className="es-lx">
        {error ? <div className="es-learn-callout is-error" role="alert">{error} <button type="button" className="es-btn es-btn-sm" onClick={() => void load()}>Try again</button></div> : null}
        {!data && !error ? <div className="es-sec-empty" role="status">Loading…</div> : null}
        {notice ? <p className="es-learn-notice" role="status">{notice}</p> : null}
        {data ? (
          view.kind === "overview" ? <Overview data={data} names={names} go={go} api={api} act={act} />
          : view.kind === "lessons" ? <LessonList data={data} names={names} go={go} back={back} />
          : view.kind === "experiences" ? <ExperienceList api={api} profiles={data.profiles} go={go} back={back} />
          : view.kind === "review" ? <ReviewList data={data} go={go} back={back} />
          : view.kind === "guide" ? <Guide data={data} lessonById={lessonById} go={go} back={back} />
          : view.kind === "lesson" ? <LessonDetail id={view.id} lesson={lessonById.get(view.id)} names={names} api={api} act={act} go={go} back={back} />
          : <ExperienceDetail episode={view.episode} lessonById={lessonById} api={api} act={act} go={go} back={back} />
        ) : null}
      </div>
    </PageShell>
  );
}

// ── overview ─────────────────────────────────────────────────────────────

function Overview({ data, names, go, api, act }: { data: Data; names: Map<string, string>; go(v: View): void; api: Api; act: Act }) {
  const pending = data.lessons.filter((l) => l.status === "pending_review");
  const inUse = data.lessons
    .filter((l) => l.status === "established" || l.status === "probation")
    .sort((a, b) => (b.last_used_at || b.updated_at) - (a.last_used_at || a.updated_at));
  const feedback = data.recent.filter(wantsFeedback);
  const needs = [
    ...pending.map((lesson) => ({ key: lesson.id, lesson })),
    ...feedback.map((episode) => ({ key: episode.id, episode })),
  ];
  return (
    <>
      {!data.status.enabled ? (
        <div className="es-learn-callout" role="note">
          {data.status.mode === "control"
            ? "Learning is in test mode: agents read unrelated notes instead of lessons, and nothing new is recorded."
            : "Learning is off, so Echo isn't recording experiences or using lessons. Turn it on in Settings › Memory."}
        </div>
      ) : null}
      <Improvement data={data} inUse={inUse} />

      {needs.length ? (
        <Section title="Needs your feedback" hint="Your answer is the evidence here: Echo won't use a lesson waiting for review, and an unchecked task only counts once you say how it went."
          more={needs.length > PREVIEW ? { label: `View all ${needs.length}`, onClick: () => go({ kind: "review" }) } : undefined}>
          {needs.slice(0, PREVIEW).map((item) =>
            "lesson" in item && item.lesson ? <LessonCard key={item.key} lesson={item.lesson} agent={names.get(item.lesson.agent_id)} onOpen={() => go({ kind: "lesson", id: item.lesson.id })} />
              : "episode" in item && item.episode ? <ExperienceCard key={item.key} episode={item.episode} ask onOpen={() => go({ kind: "experience", episode: item.episode })} /> : null,
          )}
        </Section>
      ) : null}

      <Section title="What Echo learned" hint="A lesson is an approach Echo may use again on similar tasks. It starts out being tested and is only called proven when tasks go better with it."
        more={data.lessons.length > PREVIEW ? { label: `View all ${data.lessons.length}`, onClick: () => go({ kind: "lessons" }) } : undefined}>
        {inUse.length ? inUse.slice(0, PREVIEW).map((lesson) => (
          <LessonCard key={lesson.id} lesson={lesson} agent={names.get(lesson.agent_id)} onOpen={() => go({ kind: "lesson", id: lesson.id })} />
        )) : (
          <Empty title="No lessons yet">
            Lessons come from tasks that could be checked (a file read back, a command run, a source opened) or that you marked
            Worked or Didn't work. Open <b>How learning works</b> for tasks to try.
          </Empty>
        )}
      </Section>

      <Section title="Recent experiences" hint="An experience is one task an agent attempted, and how sure EchoSpeak is that it worked."
        more={data.status.episodes > PREVIEW ? { label: `View all ${data.status.episodes}`, onClick: () => go({ kind: "experiences" }) } : undefined}>
        {data.recent.length ? data.recent.slice(0, PREVIEW).map((ep) => (
          <ExperienceCard key={ep.id} episode={ep} onOpen={() => go({ kind: "experience", episode: ep })} />
        )) : <Empty title="Nothing recorded yet">Give Echo a task, then come back here.</Empty>}
      </Section>

      <Advanced data={data} api={api} act={act} />
    </>
  );
}

/** "Is Echo improving?": an honest verdict from the control comparisons, then a quiet activity chart. */
function Improvement({ data, inUse }: { data: Data; inUse: LearningLesson[] }) {
  const compared = data.lessons.filter((l) => l.status !== "quarantined" && evidenceOf(l).compared);
  const better = compared.filter((l) => evidenceOf(l).verdict === "better").length;
  const worse = compared.filter((l) => evidenceOf(l).verdict === "worse").length;
  const checked = data.recent.filter((ep) => ep.verified_success || ep.feedback > 0).length;
  let headline: string;
  let body: string;
  if (!data.status.episodes) {
    headline = "Nothing to judge yet";
    body = "Echo hasn't recorded any tasks. Each task you give it becomes an experience here.";
  } else if (!inUse.length) {
    headline = "Recording, no lessons yet";
    body = `Echo has recorded ${plural(data.status.episodes, "experience")}. A lesson appears after a checked task gives it something worth repeating or avoiding.`;
  } else if (!compared.length) {
    headline = "Too early to tell";
    body = `Echo is trying ${plural(inUse.length, "lesson")}. To know whether one helps, EchoSpeak sometimes leaves it out on purpose and compares how those tasks go. None has enough of those comparisons yet (it needs at least 3).`;
  } else {
    headline = better ? `${better} of ${compared.length} tested lessons help` : "No lesson has shown a benefit yet";
    body = `Compared with similar tasks where the lesson was left out at random.${worse ? ` ${plural(worse, "lesson")} did worse and will be retired.` : ""} That's evidence about lessons, not proof Echo is better overall.`;
  }
  return (
    <section className="es-lx-overview" aria-labelledby="lx-improving">
      <div>
        <h2 id="lx-improving">Is Echo improving?</h2>
        <p className="es-lx-verdict">{headline}</p>
        <p className="es-lx-muted">{body}</p>
      </div>
      <dl className="es-lx-facts">
        <div><dt>Experiences</dt><dd>{data.status.episodes}</dd></div>
        <div><dt>Recently checked</dt><dd>{data.recent.length ? `${checked} of ${data.recent.length}` : "–"}</dd></div>
        <div><dt>Lessons in use</dt><dd>{inUse.length}</dd></div>
      </dl>
      {data.recent.length ? <ActivityChart episodes={data.recent} /> : null}
    </section>
  );
}

function Section({ title, hint, more, children }: { title: string; hint: string; more?: { label: string; onClick(): void }; children: React.ReactNode }) {
  const id = `lx-${title.toLowerCase().replace(/\W+/g, "-")}`;
  return (
    <section className="es-lx-section" aria-labelledby={id}>
      <div className="es-lx-section-head">
        <h2 id={id}>{title}</h2>
        {more ? <button type="button" className="es-btn es-btn-sm es-btn-quiet" onClick={more.onClick}>{more.label}</button> : null}
      </div>
      <p className="es-lx-muted">{hint}</p>
      <div className="es-lx-cards">{children}</div>
    </section>
  );
}

function Empty({ title, children }: { title: string; children: React.ReactNode }) {
  return <div className="es-lx-empty"><strong>{title}</strong><span>{children}</span></div>;
}

// ── cards: one obvious action, open the details ──────────────────────────

function LessonCard({ lesson, agent, onOpen }: { lesson: LearningLesson; agent?: string; onOpen(): void }) {
  return (
    <button type="button" className={`es-lx-card is-${lesson.status}`} onClick={onOpen}>
      <span className="es-lx-card-top">
        <span className={`es-lx-badge is-${lesson.status}`}>{LESSON_BADGE[lesson.status]}</span>
        <span className="es-lx-muted">{agent || lesson.agent_id} · {kindLabel(lesson.task_kind)}</span>
      </span>
      <strong>{lesson.kind === "avoid" ? "Avoid: " : ""}{lesson.title}</strong>
      <span className="es-lx-muted">{lessonLine(lesson)}</span>
    </button>
  );
}

function ExperienceCard({ episode, onOpen, ask }: { episode: LearningEpisode; onOpen(): void; ask?: boolean }) {
  const outcome = outcomeOf(episode);
  return (
    <button type="button" className="es-lx-card" onClick={onOpen}>
      <span className="es-lx-card-top">
        <span className={`es-lx-badge is-${outcome.tone}`}>{ask ? "Did this work?" : outcome.label}</span>
        <span className="es-lx-muted">{whoOf(episode)} · {ago(episode.created_at)}</span>
      </span>
      <strong className="es-lx-clamp">{episode.goal || "(no request text)"}</strong>
      {ask ? <span className="es-lx-muted">Nothing checked the result. Open it and say whether it worked.</span> : null}
    </button>
  );
}

// ── View all ─────────────────────────────────────────────────────────────

function ListHead({ title, back, children }: { title: string; back(): void; children?: React.ReactNode }) {
  return (
    <div className="es-lx-list-head">
      <button type="button" className="es-btn es-btn-sm es-btn-quiet" onClick={back}>← Overview</button>
      <h2>{title}</h2>
      <div className="es-learn-filters">{children}</div>
    </div>
  );
}

function Pager({ page, pages, total, onPage }: { page: number; pages: number; total: number; onPage(page: number): void }) {
  if (pages <= 1) return <p className="es-lx-muted es-lx-pager">{plural(total, "item")}</p>;
  return (
    <div className="es-lx-pager">
      <button type="button" className="es-btn es-btn-sm" disabled={page <= 0} onClick={() => onPage(page - 1)}>Newer</button>
      <span className="es-lx-muted">Page {page + 1} of {pages} · {plural(total, "item")}</span>
      <button type="button" className="es-btn es-btn-sm" disabled={page >= pages - 1} onClick={() => onPage(page + 1)}>Older</button>
    </div>
  );
}

function LessonList({ data, names, go, back }: { data: Data; names: Map<string, string>; go(v: View): void; back(): void }) {
  const [status, setStatus] = useState<"use" | "review" | "retired" | "all">("use");
  const [agent, setAgent] = useState("");
  const [page, setPage] = useState(0);
  const rows = data.lessons.filter((l) => {
    if (agent && l.agent_id !== agent) return false;
    if (status === "use") return l.status === "probation" || l.status === "established";
    if (status === "review") return l.status === "pending_review";
    if (status === "retired") return l.status === "retired" || l.status === "quarantined";
    return true;
  }).sort((a, b) => b.updated_at - a.updated_at);
  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  const shown = rows.slice(page * PAGE, page * PAGE + PAGE);
  return (
    <>
      <ListHead title="All lessons" back={back}>
        <select aria-label="Agent" value={agent} onChange={(e) => { setAgent(e.target.value); setPage(0); }}>
          <option value="">All agents</option>
          {data.profiles.map((p) => <option key={p.agent_id} value={p.agent_id}>{p.name}</option>)}
        </select>
        <div className="es-learn-seg" role="group" aria-label="Show">
          {([["use", "In use"], ["review", "Needs review"], ["retired", "Retired"], ["all", "All"]] as const).map(([value, label]) => (
            <button key={value} type="button" aria-pressed={status === value} onClick={() => { setStatus(value); setPage(0); }}>{label}</button>
          ))}
        </div>
      </ListHead>
      <div className="es-lx-cards">
        {shown.length ? shown.map((lesson) => <LessonCard key={lesson.id} lesson={lesson} agent={names.get(lesson.agent_id)} onOpen={() => go({ kind: "lesson", id: lesson.id })} />)
          : <Empty title="Nothing here">Try another filter.</Empty>}
      </div>
      <Pager page={page} pages={pages} total={rows.length} onPage={setPage} />
    </>
  );
}

function ExperienceList({ api, profiles, go, back }: { api: Api; profiles: LearningProfile[]; go(v: View): void; back(): void }) {
  const [role, setRole] = useState<"" | "own" | "worker">("");
  const [agent, setAgent] = useState("");
  const [page, setPage] = useState(0);
  const [result, setResult] = useState<{ episodes: LearningEpisode[]; total: number } | null>(null);
  const [failed, setFailed] = useState("");
  useEffect(() => {
    let live = true;
    setResult(null);
    api.episodesPage({ offset: page * PAGE, limit: PAGE, role, agentId: agent })
      .then((r) => live && (setResult(r), setFailed("")))
      .catch((err) => live && setFailed(err instanceof Error ? err.message : "Couldn't load experiences."));
    return () => { live = false; };
  }, [api, page, role, agent]);
  const pages = Math.max(1, Math.ceil((result?.total || 0) / PAGE));
  return (
    <>
      <ListHead title="All experiences" back={back}>
        <select aria-label="Agent" value={agent} onChange={(e) => { setAgent(e.target.value); setPage(0); }}>
          <option value="">All agents</option>
          {profiles.map((p) => <option key={p.agent_id} value={p.agent_id}>{p.name}</option>)}
        </select>
        <div className="es-learn-seg" role="group" aria-label="Show">
          {([["", "All"], ["own", "Replies"], ["worker", "Research workers"]] as const).map(([value, label]) => (
            <button key={value || "all"} type="button" aria-pressed={role === value} onClick={() => { setRole(value); setPage(0); }}>{label}</button>
          ))}
        </div>
      </ListHead>
      {failed ? <div className="es-learn-callout is-error" role="alert">{failed}</div> : null}
      <div className="es-lx-cards" aria-busy={!result}>
        {!result ? <div className="es-sec-empty" role="status">Loading…</div>
          : result.episodes.length ? result.episodes.map((ep) => <ExperienceCard key={ep.id} episode={ep} onOpen={() => go({ kind: "experience", episode: ep })} />)
          : <Empty title="Nothing here">Try another filter.</Empty>}
      </div>
      {result ? <Pager page={page} pages={pages} total={result.total} onPage={setPage} /> : null}
    </>
  );
}

function ReviewList({ data, go, back }: { data: Data; go(v: View): void; back(): void }) {
  const pending = data.lessons.filter((l) => l.status === "pending_review");
  const feedback = data.recent.filter(wantsFeedback);
  return (
    <>
      <ListHead title="Needs your feedback" back={back} />
      {pending.length ? <h3 className="es-lx-sub">Lessons to review ({pending.length})</h3> : null}
      <div className="es-lx-cards">{pending.map((lesson) => <LessonCard key={lesson.id} lesson={lesson} onOpen={() => go({ kind: "lesson", id: lesson.id })} />)}</div>
      {feedback.length ? <h3 className="es-lx-sub">Did these work? ({feedback.length} recent)</h3> : null}
      <div className="es-lx-cards">{feedback.map((ep) => <ExperienceCard key={ep.id} episode={ep} ask onOpen={() => go({ kind: "experience", episode: ep })} />)}</div>
      {!pending.length && !feedback.length ? <Empty title="All caught up">Nothing needs you right now.</Empty> : null}
    </>
  );
}

// ── details: where the actions live ──────────────────────────────────────

function LessonDetail({ id, lesson, names, api, act, go, back }: {
  id: string; lesson?: LearningLesson; names: Map<string, string>; api: Api; act: Act; go(v: View): void; back(): void;
}) {
  const [detail, setDetail] = useState<{ events: LearningEvent[]; episodes: LearningEpisode[] } | null>(null);
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(lesson?.title || "");
  const [text, setText] = useState(lesson?.text || "");
  const [editError, setEditError] = useState("");
  useEffect(() => {
    let live = true;
    api.lesson(id).then((d) => live && setDetail({ events: d.events, episodes: d.episodes })).catch(() => live && setDetail({ events: [], episodes: [] }));
    return () => { live = false; };
  }, [api, id, lesson?.updated_at]);
  if (!lesson) {
    return <><ListHead title="Lesson" back={back} /><Empty title="This lesson is gone">It was deleted. Its history is kept, so it can be restored from a backup of the learning database.</Empty></>;
  }
  const e = evidenceOf(lesson);
  const action = (name: "approve" | "reject" | "promote" | "retire" | "restore", label: string, primary = false, done = "") => (
    <button type="button" className={`es-btn es-btn-sm${primary ? " es-btn-primary" : ""}`} onClick={() => void act(() => api.lessonAction(lesson.id, name), done)}>{label}</button>
  );
  const save = async () => {
    setEditError("");
    try {
      await api.editLesson(lesson.id, { title, text });
      setEditing(false);
      await act(async () => undefined, "Saved.");
    } catch (err) {
      setEditError(err instanceof Error ? err.message : "Not saved.");
    }
  };
  return (
    <article className="es-lx-detail">
      <ListHead title="Lesson" back={back} />
      <div className="es-lx-card-top">
        <span className={`es-lx-badge is-${lesson.status}`}>{LESSON_BADGE[lesson.status]}</span>
        <span className="es-lx-muted">{names.get(lesson.agent_id) || lesson.agent_id} · {kindLabel(lesson.task_kind)} · learned {ago(lesson.created_at)}{lesson.edited ? " · edited by you" : ""}</span>
      </div>
      {editing ? (
        <div className="es-learn-edit">
          <label>Title<input value={title} maxLength={80} onChange={(ev) => setTitle(ev.target.value)} /></label>
          <label>Lesson<textarea value={text} maxLength={400} onChange={(ev) => setText(ev.target.value)} /></label>
          <small>Lessons can't be about permissions, approvals, safety rules, secrets, or skipping or changing checks.</small>
          {editError ? <p className="es-learn-error" role="alert">{sentence(editError)}</p> : null}
          <div>
            <button type="button" className="es-btn es-btn-sm es-btn-quiet" onClick={() => { setEditing(false); setTitle(lesson.title); setText(lesson.text); }}>Cancel</button>
            <button type="button" className="es-btn es-btn-sm es-btn-primary" disabled={!title.trim() || !text.trim()} onClick={() => void save()}>Save</button>
          </div>
        </div>
      ) : (
        <>
          <h3 className="es-lx-title">{lesson.kind === "avoid" ? "Avoid: " : ""}{lesson.title}</h3>
          <p className="es-lx-body">{lesson.text}</p>
        </>
      )}
      <p className="es-lx-muted">{lessonLine(lesson)}</p>

      <div className="es-lx-actions">
        {lesson.status === "pending_review" ? <>{action("approve", "Use this lesson", true, "Approved: Echo will try it on similar tasks.")}{action("reject", "Don't use it", false, "Rejected.")}</> : null}
        {lesson.status === "probation" ? <>{action("promote", "Mark as proven")}{action("retire", "Stop using it")}</> : null}
        {lesson.status === "established" ? (
          <>
            <button type="button" className="es-btn es-btn-sm" title="Turn this proven lesson into an Agent Skill (you approve it in Settings › Skills)"
              onClick={() => void act(() => api.skillFromLesson(lesson.id), "Made a draft skill. Review and approve it in Settings › Skills.")}>Make it a skill</button>
            {action("retire", "Stop using it")}
          </>
        ) : null}
        {lesson.status === "retired" || lesson.status === "quarantined" ? action("restore", "Use it again") : null}
        {!editing ? <button type="button" className="es-btn es-btn-sm es-btn-quiet" onClick={() => setEditing(true)}>Edit</button> : null}
        <button type="button" className="es-btn es-btn-sm es-btn-quiet" onClick={() => {
          if (window.confirm(`Delete "${lesson.title}"? Its history is kept.`)) void act(() => api.deleteLesson(lesson.id), "Deleted.").then(back);
        }}>Delete</button>
      </div>

      <section className="es-lx-block" aria-label="Does it help?">
        <h4>Does it help?</h4>
        <Compare label="With this lesson" wins={lesson.wins} total={e.decided} />
        <Compare label="Without it (left out at random)" wins={e.heldWins} total={e.held} />
        <p className="es-lx-muted">
          Now and then EchoSpeak leaves a matching lesson out on purpose, so similar tasks can be compared with and without it.
          {e.compared ? "" : ` It needs at least 3 tasks without the lesson before it can tell; this one has ${e.held}.`} Only checked results count:
          a task Echo merely said it finished counts for nothing.
        </p>
      </section>

      <section className="es-lx-block" aria-label="Where it came from">
        <h4>Where it came from</h4>
        {detail?.episodes.length ? detail.episodes.map((ep) => (
          <ExperienceCard key={ep.id} episode={ep} onOpen={() => go({ kind: "experience", episode: ep })} />
        )) : <p className="es-lx-muted">{detail ? "Those tasks are no longer kept (old experiences are trimmed)." : "Loading…"}</p>}
      </section>

      <details className="es-lx-block">
        <summary>History and undo</summary>
        {detail?.events.length ? (
          <ol className="es-learn-history">
            {detail.events.slice().reverse().map((event) => (
              <li key={event.id}>
                <span>{eventText(event)}{event.reason ? <small> · {event.reason}</small> : null}</span>
                <time>{ago(event.at)}</time>
                <button type="button" className="es-btn es-btn-sm es-btn-quiet"
                  title={event.action === "created" ? "Retire it, as if it was never learned" : "Put the lesson back the way it was before this"}
                  onClick={() => void act(() => api.rollbackLesson(lesson.id, event.id), "Undone.")}>Undo</button>
              </li>
            ))}
          </ol>
        ) : <p className="es-lx-muted">{detail ? "No history." : "Loading…"}</p>}
      </details>
    </article>
  );
}

function Compare({ label, wins, total }: { label: string; wins: number; total: number }) {
  return (
    <div className="es-lx-compare">
      <span>{label}</span>
      <span className="es-learn-bar" aria-hidden><i style={{ width: `${total ? Math.round((wins / total) * 100) : 0}%` }} /></span>
      <span className="es-lx-muted">{total ? `${wins} of ${total} worked` : "no checked tasks yet"}</span>
    </div>
  );
}

const EVENT_TEXT: Record<string, string> = {
  created: "Learned", approve: "You approved it", reject: "You rejected it", promote: "You marked it proven",
  retire: "You retired it", restore: "You restored it", edit: "You edited it", rollback: "Undone",
  deleted: "You deleted it", evidence: "Seen again", used: "Used", held_back: "Left out on purpose (comparison)",
};
function eventText(event: LearningEvent): string {
  if (event.action.startsWith("status:")) return `Became ${LESSON_BADGE[event.action.slice(7) as LessonStatus]?.toLowerCase() || event.action.slice(7)}`;
  if (event.action.startsWith("counted:")) return event.action === "counted:win" ? "Used on a task that worked" : event.action === "counted:loss" ? "Used on a task that failed" : "Used";
  if (event.action.startsWith("control:")) return event.action === "control:win" ? "Left out; the task still worked" : event.action === "control:loss" ? "Left out; the task failed" : "Left out on purpose";
  return EVENT_TEXT[event.action] || event.action;
}

function ExperienceDetail({ episode, lessonById, api, act, go, back }: {
  episode: LearningEpisode; lessonById: Map<string, LearningLesson>; api: Api; act: Act; go(v: View): void; back(): void;
}) {
  const [ep, setEp] = useState(episode);
  const outcome = outcomeOf(ep);
  const level = ep.feedback > 0 ? 4 : Math.max(0, Math.min(3, ep.level));
  const used = (ep.lessons_used || []).map((id) => lessonById.get(id)).filter(Boolean) as LearningLesson[];
  const withheld = (ep.lessons_withheld || []).map((id) => lessonById.get(id)).filter(Boolean) as LearningLesson[];
  const canRate = !isWorker(ep) && Boolean(ep.execution_id) && ep.outcome !== "error";
  const rate = (value: -1 | 0 | 1) => void act(async () => {
    await api.feedback(ep.execution_id || "", value, "", ep.agent_id);
    setEp({ ...ep, feedback: value });
  }, value > 0 ? "Thanks: counted as worked." : value < 0 ? "Thanks: counted as didn't work. Echo reviews it when idle." : "Taken back.");
  return (
    <article className="es-lx-detail">
      <ListHead title="Experience" back={back} />
      <div className="es-lx-card-top">
        <span className={`es-lx-badge is-${outcome.tone}`}>{outcome.label}</span>
        <span className="es-lx-muted">{whoOf(ep)} · {kindLabel(ep.task_kind)} · {when(ep.created_at)}</span>
      </div>
      <h3 className="es-lx-title">{ep.goal || "(no request text)"}</h3>
      {isWorker(ep) ? <p className="es-lx-muted">A research worker's part of a bigger reply. Its result is judged on its own, not by how the final answer went.</p> : null}

      {canRate ? (
        <div className="es-lx-actions" role="group" aria-label="Did this work?">
          <span>Did this work?</span>
          <button type="button" className={`es-btn es-btn-sm${ep.feedback > 0 ? " es-btn-primary" : ""}`} aria-pressed={ep.feedback > 0} onClick={() => rate(ep.feedback > 0 ? 0 : 1)}>Worked</button>
          <button type="button" className={`es-btn es-btn-sm${ep.feedback < 0 ? " es-btn-primary" : ""}`} aria-pressed={ep.feedback < 0} onClick={() => rate(ep.feedback < 0 ? 0 : -1)}>Didn't work</button>
        </div>
      ) : null}

      <section className="es-lx-block" aria-label="What was checked">
        <h4>What was checked</h4>
        <p>{LEVEL_TEXT[level]}</p>
        {ep.verification?.status && VERIFIED_TEXT[ep.verification.status] ? (
          <p>{VERIFIED_TEXT[ep.verification.status]}{ep.verification.detail || ep.verification.last ? ` ${ep.verification.detail || ep.verification.last}` : ""}
            {ep.verification.attempts ? ` (${plural(ep.verification.attempts, "attempt")})` : ""}</p>
        ) : null}
        {BLOCKED[ep.effective_cause || ""] ? <p className="es-lx-muted">This wasn't the approach's fault, so it doesn't count for or against any lesson.</p> : null}
        {ep.reasons.length ?<ul className="es-learn-reasons">{ep.reasons.map((reason, i) => <li key={i}>{reason}</li>)}</ul> : null}
        {!ep.trusted ? <p className="es-lx-muted">It read outside content ({ep.taint.join(", ")}), so anything learned from it waits for your review.</p> : null}
        {ep.feedback_note ? <p className="es-lx-muted">Your note: "{ep.feedback_note}"</p> : null}
      </section>

      {used.length || withheld.length ? (
        <section className="es-lx-block" aria-label="Lessons">
          <h4>Lessons on this task</h4>
          {used.map((lesson) => <LessonCard key={lesson.id} lesson={lesson} onOpen={() => go({ kind: "lesson", id: lesson.id })} />)}
          {withheld.length ? <p className="es-lx-muted">Left out on purpose, for comparison: {withheld.map((l) => l.title).join("; ")}.</p> : null}
        </section>
      ) : null}

      {ep.tools.length ? (
        <details className="es-lx-block">
          <summary>Steps ({ep.tools.length})</summary>
          <ol className="es-learn-tools">
            {ep.tools.slice(-20).map((tool, i) => (
              <li key={i} className={tool.not_run ? "is-skipped" : tool.ok ? "is-ok" : "is-failed"}>
                <span>{tool.label || tool.name}</span>
                <small>{tool.not_run ? "not run" : tool.ok ? "ok" : "failed"}</small>
              </li>
            ))}
          </ol>
        </details>
      ) : null}
    </article>
  );
}

// ── guide ────────────────────────────────────────────────────────────────

function Guide({ data, lessonById, go, back }: { data: Data; lessonById: Map<string, LearningLesson>; go(v: View): void; back(): void }) {
  const latest = data.recent.find((ep) => !isWorker(ep));
  const checked = data.recent.find((ep) => ep.verified_success);
  const lesson = data.lessons.slice().sort((a, b) => b.created_at - a.created_at)[0];
  const usedOne = data.lessons.filter((l) => l.uses > 0).sort((a, b) => b.last_used_at - a.last_used_at)[0];
  const tried = data.recent.find((ep) => (ep.lessons_used || []).length || (ep.lessons_withheld || []).length);
  const link = (label: string, view: View) => <button type="button" className="es-lx-link" onClick={() => go(view)}>{label}</button>;
  return (
    <article className="es-lx-guide">
      <ListHead title="How learning works" back={back} />
      <p className="es-lx-muted">Learning is different from memory. <b>Memory</b> keeps facts you tell Echo ("I prefer short answers"). <b>Learning</b> looks at how tasks actually went and keeps short lessons about what to repeat or avoid. Neither changes the AI model, its permissions or its tools.</p>
      <ol className="es-lx-steps">
        <li>
          <strong>Give Echo a task it can check.</strong>
          <span>Writing and running a script, editing a file and reading it back, or researching with sources all leave evidence. Try one of these in a chat:</span>
          <code>Write a Python script primes.py that prints the first 20 primes, then run it.</code>
          <code>Search the web for when the James Webb telescope launched and cite the page you read.</code>
        </li>
        <li>
          <strong>See the experience it recorded.</strong>
          <span>{latest ? <>Your latest: {link(`"${latest.goal.slice(0, 70)}${latest.goal.length > 70 ? "…" : ""}"`, { kind: "experience", episode: latest })} ({ago(latest.created_at)}).</> : "Nothing yet; come back after your first task."}</span>
        </li>
        <li>
          <strong>Check what was verified.</strong>
          <span>Each experience says how sure EchoSpeak is: from "Echo said it was done" up to "a check ran afterwards" or "you confirmed it". {checked ? <>A checked one: {link(`"${checked.goal.slice(0, 60)}…"`, { kind: "experience", episode: checked })}.</> : null}</span>
        </li>
        <li>
          <strong>Give feedback where it helps.</strong>
          <span>Under a reply, press Worked or Didn't work, or just say "that worked" or "that's wrong" in your next message. A few risky lessons (about sending things, or naming sites learned from the web) wait in {link("Needs your feedback", { kind: "review" })}; unreviewed ones are set aside after 30 days.</span>
        </li>
        <li>
          <strong>See whether a lesson came out of it.</strong>
          <span>When nothing else is running, Echo reviews checked tasks and failures and may write up to two short lessons. {lesson ? <>Newest: {link(lesson.title, { kind: "lesson", id: lesson.id })}.</> : "None yet."}</span>
        </li>
        <li>
          <strong>Come back in a later session.</strong>
          <span>Give a similar task. The lesson is read beforehand, or now and then left out on purpose so the two can be compared. {usedOne ? <>Last used: {link(usedOne.title, { kind: "lesson", id: usedOne.id })}, {ago(usedOne.last_used_at)}.</> : null} {tried ? <>A task where lessons were used or left out: {link(`"${tried.goal.slice(0, 50)}…"`, { kind: "experience", episode: tried })}.</> : null}</span>
        </li>
      </ol>
      <div className="es-lx-example">
        <span className="es-lx-badge">Example, not your data</span>
        <p>A lesson "Run a script once after writing it" is used on 4 coding tasks (3 worked) and left out on 3 (1 worked). That's evidence it helps, so it becomes <b>Proven</b>. If the tasks without it had gone just as well, it would stay <b>Being tested</b>; clearly better without it, and it's retired.</p>
      </div>
      {lessonById.size === 0 && !data.recent.length ? <p className="es-lx-muted">Learning needs to be on: Settings › Memory › Learning from experience.</p> : null}
    </article>
  );
}

// ── advanced, folded away ────────────────────────────────────────────────

function Advanced({ data, api, act }: { data: Data; api: Api; act: Act }) {
  const [reliability, setReliability] = useState<Record<string, ReliabilityRow[]> | null>(null);
  return (
    <details className="es-lx-section es-lx-advanced" onToggle={(e) => {
      if ((e.target as HTMLDetailsElement).open && !reliability) void api.reliability().then(setReliability).catch(() => setReliability({}));
    }}>
      <summary>Agents, tools and settings</summary>
      <p className="es-lx-muted">
        {data.status.reflections_today} of {data.status.reflection_daily_cap} reviews used today · {data.status.reflections_pending} tasks waiting to be reviewed.
        Reviews happen when no chat is running.{" "}
        <button type="button" className="es-lx-link" disabled={!data.status.enabled || !data.status.reflections_pending}
          onClick={() => void act(() => api.reflectNow(), "Reviewing finished tasks in the background. Check back in a minute.")}>Review now</button>
      </p>
      <p className="es-lx-muted">
        Reset forgets every lesson, experience and statistic. Memory is separate and isn't touched.{" "}
        <button type="button" className="es-lx-link" onClick={() => {
          if (window.confirm("Forget everything Echo learned? Memory isn't affected.")) void act(() => api.resetLearning(), "Learning was reset.");
        }}>Reset learning</button>
      </p>
      <div className="es-page-list">
        {data.profiles.map((profile) => <AgentRow key={profile.agent_id} profile={profile} onPause={(paused) => void act(() => api.pauseLearning(profile.agent_id, paused))} />)}
      </div>
      {reliability ? (
        <div className="es-learn-tables">
          <ReliabilityBars title="Tools lately" rows={reliability.tool || []} />
          <ReliabilityBars title="Search providers lately" rows={reliability.search_provider || []} />
        </div>
      ) : null}
    </details>
  );
}

function AgentRow({ profile, onPause }: { profile: LearningProfile; onPause(paused: boolean): void }) {
  const kinds = Object.entries(profile.kinds).filter(([, row]) => row.decided > 0);
  const wins = kinds.reduce((n, [, row]) => n + row.wins, 0);
  const decided = kinds.reduce((n, [, row]) => n + row.decided, 0);
  return (
    <div className="es-page-row es-learn-agent">
      <span className="es-page-row-text">
        <strong>{profile.name}</strong>
        <small>
          {decided ? `${wins} of ${decided} checked tasks worked` : "No checked tasks yet"} · {profile.lessons_proven} proven, {profile.lessons_unproven} being tested
          {profile.false_success ? ` · said it was done ${plural(profile.false_success, "time")} when it wasn't` : ""}
        </small>
      </span>
      <button type="button" className="es-btn es-btn-sm es-btn-quiet" aria-pressed={!profile.paused}
        title={profile.paused ? "Let this agent learn and use lessons again" : "Stop this agent learning or using lessons"}
        onClick={() => onPause(!profile.paused)}>{profile.paused ? "Paused · resume" : "Learning · pause"}</button>
    </div>
  );
}

type Bucket = "worked" | "unchecked" | "failed";
const bucketOf = (ep: LearningEpisode): Bucket =>
  ep.failed || ep.outcome === "error" || ep.feedback < 0 ? "failed" : ep.verified_success || ep.feedback > 0 ? "worked" : "unchecked";

/** Recent tasks per day, by how they went: checked and worked, unchecked, didn't work. */
function ActivityChart({ episodes }: { episodes: LearningEpisode[] }) {
  const days = 14;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const start = today.getTime() / 1000 - (days - 1) * 86400;
  const cols = Array.from({ length: days }, (_, i) => ({ at: start + i * 86400, worked: 0, unchecked: 0, failed: 0 }));
  for (const ep of episodes) {
    const i = Math.floor((ep.created_at - start) / 86400);
    if (i >= 0 && i < days) cols[i][bucketOf(ep)] += 1;
  }
  const peak = Math.max(1, ...cols.map((c) => c.worked + c.unchecked + c.failed));
  const totals = cols.reduce((t, c) => ({ worked: t.worked + c.worked, unchecked: t.unchecked + c.unchecked, failed: t.failed + c.failed }), { worked: 0, unchecked: 0, failed: 0 });
  return (
    <figure className="es-learn-chart es-lx-chart">
      <figcaption>
        <strong>Last 14 days</strong>
        <span className="es-learn-legend">
          <span><i className="is-worked" />Checked {totals.worked}</span>
          <span><i className="is-answered" />Not checked {totals.unchecked}</span>
          <span><i className="is-failed" />Didn't work {totals.failed}</span>
        </span>
      </figcaption>
      <div className="es-learn-cols" role="img" aria-label={`Last 14 days: ${totals.worked} checked and worked, ${totals.unchecked} not checked, ${totals.failed} didn't work`}>
        {cols.map((c) => {
          const total = c.worked + c.unchecked + c.failed;
          const day = new Date(c.at * 1000).toLocaleDateString([], { month: "short", day: "numeric" });
          return (
            <div key={c.at} className="es-learn-col" title={total ? `${day}: ${c.worked} checked, ${c.unchecked} not checked, ${c.failed} didn't work` : `${day}: nothing`}>
              <div className="es-learn-stack" style={{ height: `${(total / peak) * 100}%` }}>
                {c.failed ? <i className="is-failed" style={{ flexGrow: c.failed }} /> : null}
                {c.unchecked ? <i className="is-answered" style={{ flexGrow: c.unchecked }} /> : null}
                {c.worked ? <i className="is-worked" style={{ flexGrow: c.worked }} /> : null}
              </div>
            </div>
          );
        })}
      </div>
    </figure>
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
            return (
              <li key={row.name} title={`Lately ${row.recent_ok} of ${recent} worked · all time ${row.ok} ok, ${row.failed} failed`}>
                <span>{sentence(row.name.replace(/_/g, " "))}</span>
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

// Exported for tests.
export const _learningText = { lessonLine, outcomeOf, evidenceOf, wantsFeedback };
export const _learningViews = { Overview, LessonList, ReviewList, Guide, ExperienceDetail };
