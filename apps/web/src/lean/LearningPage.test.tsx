import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { _learningText, _learningViews } from "./LearningPage";
import { leanApi } from "./api";
import type { LearningEpisode, LearningLesson, LearningProfile, LearningStatus, LessonStatus } from "./types";

const api = leanApi("http://api");
const act = async () => undefined;
const go = () => undefined;
const now = Date.now() / 1000;

const lesson = (i: number, over: Partial<LearningLesson> = {}): LearningLesson => ({
  id: `ls_${i}`, agent_id: "echo", title: `Lesson ${i}`, text: "Run a script once after writing it.", status: "probation",
  kind: "do", task_kind: "coding", trusted: true, origin: "reflection", source_episodes: [], level: 2, uses: 0, wins: 0,
  losses: 0, last_used_at: 0, created_at: now - i * 60, updated_at: now - i * 60, edited: false, note: "", ...over,
});
const episode = (i: number, over: Partial<LearningEpisode> = {}): LearningEpisode => ({
  id: `ep_${i}`, created_at: now - i * 60, agent_id: "echo", agent_name: "Echo", goal: `Task ${i}`, outcome: "success", level: 2,
  reasons: ["ran tests after the change"], trusted: true, feedback: 0, feedback_note: "", summary: "", task_kind: "coding", taint: [],
  tamper: [], tools: [], lessons_used: [], verified_success: true, failed: false, execution_id: `ex_${i}`, role: "solo", ...over,
});
const status = (episodes: number, lessons: Partial<Record<LessonStatus, number>> = {}): LearningStatus => ({
  mode: "on", enabled: true, reflection_daily_cap: 30, reflections_today: 0, playbook_size: 4, paused_agents: [], episodes,
  lessons: { pending_review: 0, probation: 0, established: 0, retired: 0, quarantined: 0, ...lessons }, reflections_pending: 0,
});
const profiles: LearningProfile[] = [];
const names = new Map([["echo", "Echo"]]);
const count = (html: string, needle: string) => html.split(needle).length - 1;

const { Overview, LessonList, ReviewList, Guide, ExperienceDetail } = _learningViews;
const overview = (data: Parameters<typeof Overview>[0]["data"]) =>
  renderToStaticMarkup(<Overview data={data} names={names} go={go} api={api} act={act} />);

describe("Learning overview", () => {
  it("is honest and helpful with no data", () => {
    const html = overview({ status: status(0), profiles, lessons: [], recent: [] });
    expect(html.includes("Nothing to judge yet")).toBe(true);
    expect(html.includes("No lessons yet")).toBe(true);
    expect(html.includes("Nothing recorded yet")).toBe(true);
    expect(html.includes("Needs your feedback")).toBe(false);
  });

  it("shows at most three items per section, with View all", () => {
    const lessons = Array.from({ length: 120 }, (_, i) => lesson(i, { uses: 2 }));
    const recent = Array.from({ length: 40 }, (_, i) => episode(i));
    const html = overview({ status: status(300, { probation: 120 }), profiles, lessons, recent });
    expect(count(html, "es-lx-card is-probation")).toBe(3);
    expect(count(html, "Task ")).toBe(3);
    expect(html.includes("View all 120")).toBe(true);
    expect(html.includes("View all 300")).toBe(true);
  });

  it("puts lessons waiting for review first in Needs your feedback", () => {
    const lessons = [lesson(1, { status: "pending_review", title: "Check prices twice" }), lesson(2, { uses: 1 })];
    const recent = [episode(1, { verified_success: false, level: 1, outcome: "answered" })];
    const html = overview({ status: status(1, { pending_review: 1, probation: 1 }), profiles, lessons, recent });
    const feedback = html.slice(html.indexOf("Needs your feedback"), html.indexOf("What Echo learned"));
    expect(feedback.indexOf("Check prices twice") < feedback.indexOf("Did this work?")).toBe(true);
  });

  it("says when there isn't enough evidence yet instead of claiming improvement", () => {
    const tested = lesson(1, { uses: 4, wins: 3, losses: 1, held_wins: 1, held_losses: 1 });
    const html = overview({ status: status(5, { probation: 1 }), profiles, lessons: [tested], recent: [episode(1)] });
    expect(html.includes("Too early to tell")).toBe(true);
    expect(_learningText.lessonLine(tested).includes("Not enough evidence yet")).toBe(true);
  });

  it("reports the control comparison once there is enough of it", () => {
    const better = lesson(1, { uses: 5, wins: 4, losses: 1, held_wins: 1, held_losses: 2, status: "established" });
    const html = overview({ status: status(8, { established: 1 }), profiles, lessons: [better], recent: [episode(1)] });
    expect(html.includes("1 of 1 tested lessons help")).toBe(true);
    expect(_learningText.lessonLine(better)).toBe("Helps: 4 of 5 checked tasks worked with it, 1 of 3 without it.");
  });
});

describe("Learning records", () => {
  it("labels research workers and never asks feedback on them", () => {
    const worker = episode(1, { role: "worker", source: "swarm", verified_success: false, level: 1 });
    expect(_learningText.wantsFeedback(worker)).toBe(false);
    const html = renderToStaticMarkup(<ExperienceDetail episode={worker} lessonById={new Map()} api={api} act={act} go={go} back={go} />);
    expect(html.includes("research worker")).toBe(true);
    expect(html.includes("Did this work?")).toBe(false);
  });

  it("explains what was checked in plain words, and offers feedback on an unchecked reply", () => {
    const unchecked = episode(2, { verified_success: false, level: 1, outcome: "answered" });
    expect(_learningText.outcomeOf(unchecked).label).toBe("Done, but not checked");
    const html = renderToStaticMarkup(<ExperienceDetail episode={unchecked} lessonById={new Map()} api={api} act={act} go={go} back={go} />);
    expect(html.includes("Tools ran and worked, but nothing checked the result afterwards.")).toBe(true);
    expect(html.includes("Did this work?")).toBe(true);
  });

  it("shows a blocker as the setup's fault and a contradicted claim as a failure, with what was verified", () => {
    const blocked = episode(3, { outcome: "failure", failed: false, verified_success: false, level: 1, effective_cause: "environment",
      verification: { status: "blocked", detail: "", bottleneck: "environment" } });
    expect(_learningText.outcomeOf(blocked)).toEqual({ label: "Blocked by the setup", tone: "neutral" });
    const html = renderToStaticMarkup(<ExperienceDetail episode={blocked} lessonById={new Map()} api={api} act={act} go={go} back={go} />);
    expect(html.includes("count for or against any lesson")).toBe(true);
    expect(html.includes("Blocked before it could be verified.")).toBe(true);
    const claimed = episode(4, { failed: true, verified_success: false, effective_cause: "false_success",
      verification: { status: "failing", last: "test `pytest -q`: 1 failed, 4 passed, exit 1", attempts: 2 } });
    expect(_learningText.outcomeOf(claimed).label).toBe("Said done, but the checks disagreed");
    const detail = renderToStaticMarkup(<ExperienceDetail episode={claimed} lessonById={new Map()} api={api} act={act} go={go} back={go} />);
    expect(detail.includes("The last check failed. test `pytest -q`: 1 failed, 4 passed, exit 1 (2 attempts)")).toBe(true);
  });

  it("pages long lesson lists instead of rendering them all", () => {
    const lessons = Array.from({ length: 45 }, (_, i) => lesson(i, { uses: 1 }));
    const html = renderToStaticMarkup(<LessonList data={{ status: status(0), profiles, lessons, recent: [] }} names={names} go={go} back={go} />);
    expect(count(html, "es-lx-card ")).toBe(20);
    expect(html.includes("Page 1 of 3")).toBe(true);
  });

  it("review list shows everything waiting, and says when nothing is", () => {
    const empty = renderToStaticMarkup(<ReviewList data={{ status: status(0), profiles, lessons: [], recent: [] }} go={go} back={go} />);
    expect(empty.includes("All caught up")).toBe(true);
  });

  it("guide links to real records and marks its example as an example", () => {
    const html = renderToStaticMarkup(<Guide data={{ status: status(1), profiles, lessons: [lesson(1)], recent: [episode(1)] }}
      lessonById={new Map()} go={go} back={go} />);
    expect(html.includes("Task 1")).toBe(true);
    expect(html.includes("Lesson 1")).toBe(true);
    expect(html.includes("Example, not your data")).toBe(true);
    expect(html.includes("Learning is different from memory")).toBe(true);
  });
});
