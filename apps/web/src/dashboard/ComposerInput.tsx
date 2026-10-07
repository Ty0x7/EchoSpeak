import React, { useState } from "react";
import { ContextMeter } from "../app/chatComponents";
import type { Message, ProviderInfo, ThreadSessionState } from "../app/types";
import { MentionMenu, activeMention, mentionMatches } from "../lean/Dialogs";
import type { LeanPersona, LeanRoom } from "../lean/types";

type Mention = { start: number; query: string; index: number } | null;

type ComposerInputProps = {
  threads: { id: string; name: string }[];
  projects: { id: string; git_metadata?: Record<string, any> }[];
  activeThreadId: string;
  activeProjectId: string;
  threadState: ThreadSessionState | null;
  providerError: string | null;
  folderPathFromDrop(event: React.DragEvent): string;
  attachFolder(path?: string): void;
  onRemoveFolder(): void;
  textareaRef: React.RefObject<HTMLTextAreaElement>;
  input: string;
  onInput(value: string): void;
  onSend(): void;
  activeRoom: LeanRoom | null;
  roomMembers: LeanPersona[];
  mention: Mention;
  setMention(value: Mention): void;
  messages: Message[];
  providerInfo: ProviderInfo | null;
  /** Thinking, dictation, voice and options: the row under the text box, inside the card. */
  toolbar?: React.ReactNode;
};

export function ComposerInput({ threads, projects, activeThreadId, activeProjectId, threadState,
  providerError, folderPathFromDrop, attachFolder, onRemoveFolder, textareaRef, input, onInput, onSend,
  activeRoom, roomMembers, mention, setMention, messages, providerInfo, toolbar }: ComposerInputProps) {
  const [folderDropActive, setFolderDropActive] = useState(false);
  return (
                    <div className="input-row">
                      <div className="composer-input-stack composer-card">
                        <div
                          className={"session-folder-strip" + (folderDropActive ? " is-drop-active" : "")}
                          aria-label="Session and Project folder attachment. Drop a local folder here to create or select its Project."
                          onDragEnter={(event) => { event.preventDefault(); setFolderDropActive(true); }}
                          onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = "link"; setFolderDropActive(true); }}
                          onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setFolderDropActive(false); }}
                          onDrop={(event) => { event.preventDefault(); setFolderDropActive(false); const path = folderPathFromDrop(event); if (path) void attachFolder(path); else void attachFolder(); }}
                        >
                          <span style={{ whiteSpace: "nowrap" }}>
                            Session: <b style={{ color: "rgba(var(--es-ink-rgb), 0.8)" }}>{threads.find(t => t.id === activeThreadId)?.name || activeThreadId}</b>
                          </span>
                          {(() => {
                            const folderFull =
                              String(threadState?.workspace_root || threadState?.project_path || "").trim();
                            const folderName = folderFull
                              ? folderFull.replace(/[\\/]+$/, "").split(/[/\\]/).filter(Boolean).pop() || folderFull
                              : "";
                            const gitBranch = projects.find(project => project.id === activeProjectId)?.git_metadata?.is_repository
                              ? String(projects.find(project => project.id === activeProjectId)?.git_metadata?.branch || "repository")
                              : "";
                            return (
                              <button
                                type="button"
                                onClick={() => void attachFolder()}
                                title={
                                  folderFull
                                    ? folderFull
                                    : "Choose or drop a local folder; folders become Projects automatically"
                                }
                                style={{
                                  border: 0,
                                  background: "transparent",
                                  color: "inherit",
                                  padding: 0,
                                  font: "inherit",
                                  cursor: "pointer",
                                  textAlign: "left",
                                  whiteSpace: "nowrap",
                                }}
                              >
                                Folder:{" "}
                                <b style={{ color: "rgba(var(--es-ink-rgb), 0.8)" }}>
                                  {folderName || "drop folder to start Project"}
                                  {gitBranch ? ` · git:${gitBranch}` : ""}
                                </b>
                              </button>
                            );
                          })()}
                          {(threadState?.workspace_root || threadState?.project_path) && (
                            <button
                              type="button"
                              aria-label="Remove folder from this Session"
                              title="Remove folder from this Session"
                              onClick={() => void onRemoveFolder()}
                              style={{ width: 18, height: 18, border: 0, background: "transparent", color: "rgba(var(--es-ink-rgb), 0.65)", borderRadius: 2, cursor: "pointer", lineHeight: 1, flexShrink: 0 }}
                            >
                              ×
                            </button>
                          )}
                          {providerError ? (
                            <span role="status" title={providerError} style={{ marginLeft: 8, color: "var(--es-warn)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", minWidth: 0 }}>
                              {providerError}
                            </span>
                          ) : null}
                        </div>
                        <textarea
                          ref={textareaRef}
                          className="input-field"
                          value={input}
                          rows={1}
                          disabled={!activeThreadId}
                          onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => {
                            onInput(e.target.value);
                            if (activeRoom?.kind === "group") {
                              const found = activeMention(e.target.value, e.target.selectionStart ?? e.target.value.length);
                              setMention(found && mentionMatches(found.query, roomMembers).length ? { ...found, index: 0 } : null);
                            }
                          }}
                          onKeyDown={(e: React.KeyboardEvent<HTMLTextAreaElement>) => {
                            if (mention) {
                              const matches = mentionMatches(mention.query, roomMembers);
                              if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                                e.preventDefault();
                                const step = e.key === "ArrowDown" ? 1 : -1;
                                setMention({ ...mention, index: (mention.index + step + matches.length) % Math.max(1, matches.length) });
                                return;
                              }
                              if ((e.key === "Enter" || e.key === "Tab") && matches[mention.index]) {
                                e.preventDefault();
                                const pick = matches[mention.index];
                                const caret = e.currentTarget.selectionStart ?? input.length;
                                const next = `${input.slice(0, mention.start)}@${pick.name} ${input.slice(caret)}`;
                                onInput(next);
                                setMention(null);
                                return;
                              }
                              if (e.key === "Escape") {
                                setMention(null);
                                return;
                              }
                            }
                            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                              e.preventDefault();
                              void onSend();
                            }
                          }}
                          onBlur={() => window.setTimeout(() => setMention(null), 120)}
                          placeholder={
                            !activeThreadId
                              ? "Create a Session with + to chat"
                              : activeRoom?.kind === "group"
                              ? `Message ${activeRoom.name}  ·  @ to pick who answers`
                              : activeRoom
                              ? `Message ${roomMembers[0]?.name || activeRoom.name}`
                              : "Ask Echo anything..."
                          }
                          aria-label="Message"
                        />
                        {mention && activeRoom?.kind === "group" ? (
                          <MentionMenu
                            anchor={textareaRef.current}
                            query={mention.query}
                            agents={roomMembers}
                            activeIndex={mention.index}
                            onPick={(pick) => {
                              const caret = textareaRef.current?.selectionStart ?? input.length;
                              onInput(`${input.slice(0, mention.start)}@${pick.name} ${input.slice(caret)}`);
                              setMention(null);
                              textareaRef.current?.focus();
                            }}
                          />
                        ) : null}
                      <div className="composer-bottom">
                        {toolbar}
                        <span className="composer-spacer" />
                      <div className="composer-trailing">
                        <ContextMeter messages={messages} contextWindow={providerInfo?.context_window || 0} model={providerInfo?.model} />
                        <button
                          className="send-button"
                          onClick={() => void onSend()}
                          type="button"
                          disabled={!activeThreadId || !input.trim()}
                          title="Send"
                          aria-label="Send message"
                        >
                          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden>
                            <path d="M5 12L19 12M19 12L13 6M19 12L13 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                          </svg>
                        </button>
                      </div>
                      </div>
                      </div>
                    </div>
  );
}