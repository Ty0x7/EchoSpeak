"""
Core agent module for Echo Speak.
Implements the conversational AI agent with memory and tools.
Supports multiple LLM providers: OpenAI, Ollama, LM Studio, LocalAI, llama.cpp, vLLM.
"""

import importlib.util
import hashlib
import ast
from dataclasses import dataclass
import json
import os
import re
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List, Iterable
from loguru import logger

from pydantic import BaseModel, Field
from typing import List, Any, Dict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RESEARCH_TOOL_NAMES = {
    "web_search",
    "youtube_transcript",
    "browse_task",
}

AUTOMATION_TOOL_NAMES = {
    "desktop_list_windows",
    "desktop_find_control",
    "desktop_click",
    "desktop_type_text",
    "desktop_activate_window",
    "desktop_send_hotkey",
    "open_chrome",
    "open_application",
    "file_list",
    "file_read",
    "file_write",
    "file_move",
    "file_copy",
    "file_delete",
    "file_mkdir",
    "artifact_write",
    "analyze_screen",
    "vision_qa",
    "take_screenshot",
    "notepad_write",
    "terminal_run",
}

_MATH_NAMES = {
    "abs", "max", "min", "pow", "round", "sum", "len",
    "sqrt", "sin", "cos", "tan", "log", "log10", "pi", "e",
}
_MATH_AST_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Constant,
    ast.List,
    ast.Tuple,
    ast.Call,
    ast.Name,
    ast.Load,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.UAdd,
    ast.USub,
)


def _is_valid_math_expression(expression: str) -> bool:
    """Accept only the calculator's documented mathematical expression subset."""
    value = str(expression or "").strip()
    if not value or len(value) > 500:
        return False
    try:
        tree = ast.parse(value, mode="eval")
    except (SyntaxError, ValueError, TypeError):
        return False
    for node in ast.walk(tree):
        if not isinstance(node, _MATH_AST_NODES):
            return False
        if isinstance(node, ast.Name) and node.id not in _MATH_NAMES:
            return False
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _MATH_NAMES:
                return False
            if node.keywords:
                return False
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            return False
    return True

_TRACE_LOCK = threading.Lock()


from config import config, ModelProvider, DATA_DIR
from agent.memory import get_agent_memory
from agent.research import SearchGrounder, format_grounded_tool_output
from agent.session_memory import SessionMemoryDistiller
from agent.mode_controller import ModeDecision, tool_allowed_by_mode

from agent.skills_registry import (
    build_skills_prompt,
    load_skills,
    load_skill_tools,
    load_skill_plugin,
    load_workspace,
    merge_tool_allowlists,
)
from agent.tools import get_available_tools, TOOL_METADATA
from agent.tool_registry import ToolRegistry
from agent.router import IntentRouter
from agent.state import ProjectLedgerEntry, ThreadSessionState, ToolOutcome, get_state_store
from agent.model_adapters import get_family_adapter
from agent.model_runtime import ModelRuntimeClient
from agent.update_context import ensure_update_context_plugin_registered
from agent.verification import VerificationTelemetry

ensure_update_context_plugin_registered()

SYSTEM_PROMPT_BASE = (
    "You are Echo Speak, a conversational AI companion. "
    "Default to natural, friendly replies that feel like a quick chat. "
    "Do not add recaps, summaries, or 'next steps' unless the user explicitly asks. "
    "Keep responses concise and avoid boilerplate acknowledgments unless the user invites it. "
    "Mirror the user's tone; if they sound excited, you can open with a brief, warm reaction. "
    "Use lists or headings only when the user requests them or when needed for clarity. "
    "If you use tools, weave results into a short, conversational answer without report-style formatting. "
    "For any time-sensitive facts (news, sports, prices, schedules, ongoing events, 'this year', 'latest'), prefer using web_search rather than relying on memory or model knowledge. "
    "When calling web_search, pass a compact factual query with the specific anchors (teams/places/products, date or today/tomorrow, and what fact is needed: kickoff, score, high/low, price, release). "
    "Never search raw chat fragments, politeness ('please check'), or mid-sentence debris — one clear search string per independent fact ask. "
    "Treat memory/context as potentially stale; if it conflicts with fresh web results, trust the web results. "
    "When the user asks to code, build, create, inspect, or modify files, act like a coding assistant: plan briefly, use file/terminal tools when available, and explain exact blockers instead of saying you cannot. "
    "If the user says Desktop as a file destination, treat it as the filesystem Desktop, not as a request to see their screen."
)


@dataclass(frozen=True)
class TurnExecutionAuthority:
    """Immutable authority captured for one canonical semantic Turn.

    Durable ThreadSessionState may change as progress is recorded, but those
    writes cannot redefine the tools, constraints, model, or scope exposed to
    the active model loop. Every invocation still revalidates current mutable
    policy and inventory before execution.
    """

    session_id: str
    project_id: str
    project_path: str
    provider_id: str
    model_id: str
    model_binding_revision: int
    inventory_revision: int
    inventory_sha256: str
    mode: str
    allowed_tool_names: frozenset[str]
    constraints: frozenset[str]
    permissions: tuple[tuple[str, bool], ...]
    bound_at: float

    def safe_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "project_id": self.project_id,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "model_binding_revision": self.model_binding_revision,
            "inventory_revision": self.inventory_revision,
            "inventory_sha256": self.inventory_sha256,
            "mode": self.mode,
            "allowed_tool_names": sorted(self.allowed_tool_names),
            "constraints": sorted(self.constraints),
            "bound_at": self.bound_at,
        }


class ConversationMemory(BaseModel):
    """Simple conversation memory for agent interactions."""
    messages: List[Dict[str, str]] = Field(default_factory=list)
    memory_key: str = "chat_history"

    def load_memory_variables(self, inputs: Dict[str, Any] = None) -> Dict[str, Any]:
        return {self.memory_key: self.messages}

    def save_context(self, inputs: Dict[str, Any], outputs: Dict[str, str]) -> None:
        if "input" in inputs:
            self.messages.append({"role": "human", "content": inputs["input"]})
        if "output" in outputs:
            self.messages.append({"role": "ai", "content": outputs["output"]})

    def clear(self) -> None:
        self.messages = []


class Tool:
    """Small provider-neutral tool wrapper used by the registry bridge."""

    def __init__(self, name: str, func: Any, description: str):
        self.name = name
        self.func = func
        self.description = description

    def run(self, **kwargs: Any) -> str:
        try:
            result = self.func(**kwargs)
            if result is None:
                return "Tool executed successfully."
            return str(result)
        except Exception as exc:
            return f"Error: {exc}"

    def invoke(self, **kwargs: Any) -> str:
        return self.run(**kwargs)


class AuthorityCheckedTool:
    """Expose one raw tool only through Echo's canonical execution boundary."""

    def __init__(self, agent: "EchoSpeakAgent", raw_tool: Any):
        self._agent = agent
        self._raw_tool = raw_tool
        self.name = str(getattr(raw_tool, "name", "") or "")
        self.description = str(getattr(raw_tool, "description", "") or "")
        self.args_schema = getattr(raw_tool, "args_schema", None)
        self.return_direct = bool(getattr(raw_tool, "return_direct", False))

    def invoke(self, input: Any = None, **kwargs: Any) -> str:  # noqa: A002
        return self.invoke_outcome(input, **kwargs).user_text()

    def invoke_outcome(
        self,
        input: Any = None,  # noqa: A002
        **kwargs: Any,
    ) -> ToolOutcome:
        if isinstance(input, dict):
            params = {**input, **kwargs}
        elif input is not None and not kwargs:
            if self.name == "calculate":
                params = {"expression": input}
            elif self.name == "web_search":
                params = {"q": input}
            else:
                params = {"input": input}
        else:
            params = dict(kwargs)
        return self._agent._invoke_authorized_raw_tool(self._raw_tool, params)

    def run(self, *args: Any, **kwargs: Any) -> str:
        if args and not kwargs:
            return self.invoke(args[0])
        return self.invoke(**kwargs)

    def __call__(self, *args: Any, **kwargs: Any) -> str:
        return self.run(*args, **kwargs)


class WebEvidenceHeuristics:
    """
    Pure quality predicates for web-search evidence.

    Acquisition and retry ownership stays in the canonical requirement/research
    runtime. These helpers classify already-returned evidence only; they never
    invoke a provider, execute a tool, or advance TaskRun state.
    """

    def __init__(self, agent_core):
        self.agent = agent_core
        self._today_date: Optional[str] = None
    
    def _get_today_date(self) -> str:
        """Extract today's date YYYY-MM-DD from system time."""
        if self._today_date:
            return self._today_date
        # Try to get from agent's cached time context
        cached_time = str(getattr(self.agent, "_cached_time_context", "") or "")
        if cached_time:
            m = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", cached_time)
            if m:
                self._today_date = m.group(1)
                return self._today_date
        # Fallback: use datetime
        from datetime import datetime
        self._today_date = datetime.now().strftime("%Y-%m-%d")
        return self._today_date
    
    def _is_next_upcoming_query(self, q: str) -> bool:
        """Check if query is asking for 'next' or 'upcoming' schedule."""
        low = (q or "").lower()
        if not low.strip():
            return False
        try:
            return bool(self.agent._is_next_upcoming_schedule_query(low))
        except Exception:
            if not any(t in low for t in ["next", "upcoming"]):
                return False
            schedule_terms = ["game", "match", "event", "show", "episode", "launch", "release", "flight", "departure", "concert", "fixture", "play", "plays"]
            return any(t in low for t in schedule_terms)

    def _extract_dates_from_result(self, result: str) -> List[str]:
        """Extract YYYY-MM-DD dates from search result."""
        filtered_lines = []
        for line in (result or "").splitlines():
            stripped = line.strip()
            if stripped.startswith("Date:") or stripped.startswith("URL:"):
                continue
            filtered_lines.append(line)
        cleaned = "\n".join(filtered_lines)
        try:
            parsed = self.agent._extract_dates_from_text(cleaned, default_year=int(self._get_today_date()[:4]))
            return sorted({d.strftime("%Y-%m-%d") for d in parsed})
        except Exception:
            return re.findall(r"\b(20\d{2}-\d{2}-\d{2})\b", cleaned)
    
    def _has_stale_date(self, result: str) -> bool:
        """Check if result contains ONLY dates earlier than today.

        For schedule queries, search results almost always contain a mix of
        past game scores and future schedule dates.  We only flag the result
        as stale when every extracted date is in the past — meaning the
        results have no upcoming-schedule data at all.
        """
        today = self._get_today_date()
        dates = self._extract_dates_from_result(result)
        if not dates:
            return False
        has_past = any(d < today for d in dates)
        has_future_or_today = any(d >= today for d in dates)
        # A mix of past + future is normal; only flag pure-past results.
        return has_past and not has_future_or_today
    
    def _is_market_query(self, q: str) -> bool:
        """Detect market/odds queries."""
        low = (q or "").lower()
        return any(t in low for t in ["odds", "polymarket", "betting", "market", "price", "prediction market"])

    def _is_live_score_query(self, q: str) -> bool:
        low = (q or "").lower()
        # Product/commerce "live price" is NOT a sports score (live bug: Silksong price →
        # "live price today live score result").
        if re.search(
            r"\b(price|cost|msrp|pre-?order|stock|bitcoin|btc|crypto|usd|release|trailer|"
            r"steam|editions?)\b",
            low,
        ) and not re.search(r"\b(game score|match score|final score|who won|winning)\b", low):
            return False
        # Bare "live" alone is too weak (matches "live price"); need real score language
        has_score_lang = any(
            t in low
            for t in ("score", "scores", "who won", "winning", "live score", "current score")
        )
        if not has_score_lang and "result" not in low and "results" not in low:
            return False
        if has_score_lang:
            pass
        elif re.search(r"\b(result|results)\b", low) and not re.search(
            r"\b(game|match|fixture|vs\.?|versus|nhl|nba|nfl|mlb|fifa)\b", low
        ):
            # "search result" / "price result" without sports → not live score
            return False
        sport_terms = [
            "game", "match", "fixture", "fifa", "world cup", "soccer", "football",
            "nhl", "nba", "nfl", "mlb", "wnba", "hockey", "basketball", "vs", "versus",
        ]
        if any(t in low for t in sport_terms):
            return True
        # Free-form team phrase only when score/result language is already present
        if has_score_lang or re.search(r"\b(score|scores)\b", low):
            try:
                from agent.research import _extract_teamish_phrase, _extract_vs_sides
                if _extract_vs_sides(low) or _extract_teamish_phrase(low):
                    return True
            except Exception:
                pass
        return False

    def _live_score_result_looks_relevant(self, q: str, result: str) -> bool:
        low_result = (result or "").lower()
        if not low_result.strip():
            return False

        # Search snippets that only discuss dates/schedules are often a miss
        # for "what is the score right now?" style prompts.
        score_signals = [
            "score", "final", "live", "result", "ft", "full-time", "halftime",
            "half-time", "1-0", "0-1", "2-0", "0-2", "1-1", "2-1", "1-2",
            "3-0", "0-3", "3-1", "1-3", "penalty", "goals",
        ]
        if any(sig in low_result for sig in score_signals):
            return True

        # Generic numeric score pattern near team/game language.
        if re.search(r"\b\d{1,2}\s*[-:]\s*\d{1,2}\b", low_result):
            return True

        date_only_signals = ["schedule", "date", "kickoff", "kick-off", "starts", "start time"]
        if any(sig in low_result for sig in date_only_signals):
            return False

        return False

    def _is_schedule_or_fixture_query(self, q: str) -> bool:
        low = (q or "").lower()
        return bool(
            re.search(
                r"\b(schedule|fixture|fixtures|matchups?|kickoff|who(?:'s| is)? playing|"
                r"games? today|matches? today|world cup|fifa)\b",
                low,
            )
        )

    def _is_timezone_query(self, q: str) -> bool:
        low = (q or "").lower()
        return bool(
            re.search(
                r"\b(timezone|time zone|mnt|mst|mdt|mountain|my time|local time|convert)\b",
                low,
            )
        )

    def _is_grounded_packet_acceptable(self, q: str, result: str) -> bool:
        """Quality gate for already-grounded search packets (must not no-op)."""
        low = (result or "").lower()
        if not low or len(low) < 40:
            return False
        if "search_evidence_insufficient" in low or "accepted=false" in low:
            # Soft-accept packets still may carry usable snippets — only reject hard insufficient
            if "search_evidence_insufficient" in low and len(low) < 280:
                return False
        if self._is_live_score_query(q):
            return self._live_score_result_looks_relevant(q, result)
        if self._is_schedule_or_fixture_query(q):
            # Structural matchup detection (A vs B) — not a team-name whitelist
            has_sides = bool(re.search(r"\bvs\.?\b", low)) or bool(
                re.search(r"\b\w{3,}\s+versus\s+\w{3,}\b", low)
            )
            has_clock = bool(
                re.search(r"\b\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?|am|pm)", low)
                or re.search(r"\b\d{1,2}:\d{2}\b", low)
            )
            # Concrete matchup +/or kickoff is enough even if packet is short
            if has_sides or has_clock:
                return True
            # Tournament fluff ("104 games", "full schedule") without names/times is a miss
            if re.search(
                r"\b(104 games|full schedule|across canada|you can find|"
                r"where to watch|lamine yamal|cristiano ronaldo playing)\b",
                low,
            ) and not (has_sides and has_clock):
                return False
            if not has_sides and not has_clock and len(low) < 600:
                return False
        if self._is_timezone_query(q):
            # Need a time or an explicit conversion mention with numbers
            if not re.search(r"\b\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?|am|pm)", low):
                if not re.search(r"\b\d{1,2}:\d{2}\b", low):
                    return False
        if self._has_stale_date(result) and self._is_next_upcoming_query(q):
            return False
        return len(result) > 120
    
class EchoSpeakAgent:
    """Main conversational agent for Echo Speak."""

    def __init__(self, memory_path: Optional[str] = None, llm_provider: ModelProvider = None, manage_background_services: bool = True, model_id: Optional[str] = None):
        logger.info("Initializing Echo Speak Agent...")
        default_cloud_provider = str(getattr(config, "default_cloud_provider", ModelProvider.OPENAI.value) or "").strip().lower()
        openai_key = str(getattr(getattr(config, "openai", None), "api_key", "") or "").strip()
        gemini_key = str(getattr(getattr(config, "gemini", None), "api_key", "") or "").strip()
        if default_cloud_provider == ModelProvider.GEMINI.value:
            fallback_provider = ModelProvider.GEMINI if gemini_key or not openai_key else ModelProvider.OPENAI
        elif default_cloud_provider == ModelProvider.OPENAI.value:
            fallback_provider = ModelProvider.OPENAI if openai_key or not gemini_key else ModelProvider.GEMINI
        elif gemini_key and not openai_key:
            fallback_provider = ModelProvider.GEMINI
        else:
            fallback_provider = ModelProvider.OPENAI
        self.llm_provider = llm_provider or (config.local.provider if config.use_local_models else fallback_provider)
        self._bound_model_id = str(model_id or "").strip()
        self.model_runtime = ModelRuntimeClient(self.llm_provider, self._bound_model_id)
        # Read-only compatibility projection for older prompt/context helpers.
        # The SessionModelBinding remains authoritative; this projection is
        # created from the exact client bound to this pooled Session agent.
        self.provider_info = {
            "provider": self.llm_provider.value,
            "model": self.model_runtime.model_id,
        }
        self._model_context_snapshot: str = ""
        self._model_latest_user_message: str = ""
        self._mode_llm_cache: Dict[str, Any] = {}

        # A Session has one selected model. Research, coding, and chat change
        # tools/evidence policy, not providers or model identity.
        self.research_model_runtime = None
        # Thread-pooled agents share the one canonical in-process memory owner;
        # per-agent vector-store snapshots could otherwise overwrite each other.
        self.memory = get_agent_memory(memory_path)
        self.conversation_memory = ConversationMemory()
        self._thread_conversation_memories: Dict[str, ConversationMemory] = {
            "default": self.conversation_memory,
        }
        self._thread_summaries: Dict[str, str] = {}
        self._summary: str = ""
        self.document_store = None
        if getattr(config, "document_rag_enabled", False):
            if self.memory.embeddings is None:
                logger.warning("Document RAG disabled: embeddings unavailable")
            else:
                try:
                    from agent.document_store import DocumentStore

                    self.document_store = DocumentStore(
                        self.memory.embeddings,
                        str(getattr(config, "docs_index_path", "")),
                        str(getattr(config, "docs_meta_path", "")),
                    )
                except Exception as exc:
                    logger.warning(f"Document RAG disabled: {exc}")
        self._last_doc_sources: list[dict[str, Any]] = []
        self._pending_action: Optional[Dict[str, Any]] = None
        self._active_approved_action: Optional[Dict[str, Any]] = None
        self._active_retry_action: Optional[Dict[str, Any]] = None
        self._last_boundary_outcome: Optional[ToolOutcome] = None
        self._tool_outcomes_by_run_id: Dict[str, ToolOutcome] = {}
        self._registered_tool_runs: Dict[str, List[tuple[str, str]]] = {}
        self._boundary_record_in_progress: bool = False
        self._last_boundary_record: Optional[tuple[str, str, str]] = None
        self._requested_approval_id: Optional[str] = None
        self._turn_cancel_event: Optional[threading.Event] = None
        self._pending_detail: Optional[Dict[str, str]] = None
        self._last_tts_text: str = ""
        self._trace_enabled = bool(getattr(config, "trace_enabled", False))
        trace_path = str(getattr(config, "trace_path", "") or "").strip()
        self._trace_path = Path(trace_path) if trace_path else None
        self._last_trace_id: Optional[str] = None
        self._last_memory_mode: Optional[str] = None
        self._last_memory_thread_id: Optional[str] = None
        self._last_web_query_context: str = ""
        self._current_subject_text: str = ""
        self._last_grounded_search_result: Optional[Dict[str, Any]] = None
        self._last_context_budget_report: Optional[Dict[str, Any]] = None
        telemetry_enabled = bool(getattr(config, "verification_telemetry_enabled", True))
        telemetry_path = DATA_DIR / "verification_events.jsonl" if telemetry_enabled else None
        self._verification_telemetry = VerificationTelemetry(path=telemetry_path, enabled=telemetry_enabled)
        self._session_memory = SessionMemoryDistiller(
            DATA_DIR,
            update_turns=int(getattr(config, "session_memory_update_turns", 1) or 1),
        )
        try:
            from agent.active_work import ActiveWorkStore

            self._active_work_store = ActiveWorkStore()
        except Exception:
            self._active_work_store = None
        self._last_stage4_branch: str = ""
        self._last_tool_calling_mode: str = ""
        self._current_thread_id: str = "default"
        self._current_execution_id: Optional[str] = None
        self._current_request_id: Optional[str] = None
        self._current_mode_decision: Optional[ModeDecision] = None
        self._turn_execution_authority: Optional[TurnExecutionAuthority] = None
        self._current_callbacks: list = []
        self._emitted_reasoning_hashes: set[str] = set()
        self._state_store = get_state_store()
        self._execution_context = ThreadSessionState(thread_id="default")
        self._tool_context_token = None
        self._soul_cache: Dict[str, Any] = {"path": "", "mtime_ns": -1, "max_chars": 0, "content": ""}
        self._workspace_id: Optional[str] = None
        self._workspace_name: str = ""
        self._workspace_prompt: str = ""
        self._skills_prompt: str = ""
        self._tool_allowlist_override: Optional[set[str]] = None
        self._active_project_id: Optional[str] = None
        self.lc_tools: List[Any] = []
        self.tools: List[Any] = []
        self._tool_inventory_snapshot: Dict[str, Any] = {}
        self._initializing_tool_inventory = True
        self._action_parser_enabled = bool(getattr(config, "action_parser_enabled", True))
        # Track canonical ToolOutcomes for the active bounded model loop.
        self._partial_tool_results: List[Dict[str, Any]] = []
        self._partial_tool_names: Dict[str, str] = {}  # run_id → tool_name
        self._partial_tool_inputs: Dict[str, str] = {}  # run_id → tool input
        # Cross-source activity tracking
        self._last_activity: Dict[str, Any] = {"source": None, "summary": "", "thread_id": None, "at": 0.0}
        self._thread_last_activity: Dict[str, Dict[str, Any]] = {}
        # Discord user identity & role (set per-request in process_query)
        self._discord_user_info: Optional[Dict[str, Any]] = None
        self._current_user_role: str = "owner"  # Default to owner for non-Discord sources
        self._request_lock = threading.RLock()
        self._request_result_local = threading.local()
        self._canonical_semantic_flow = False
        self._active_task_run = None
        self._active_turn_interpretation = None
        self._raw_turn_user_message = ""
        self._cached_time_context = ""
        self._web_evidence_heuristics = WebEvidenceHeuristics(self)
        self._router: Optional[IntentRouter] = None  # Set after tools are built
        # Populate the global tool registry from the legacy lists (migration bridge)
        ToolRegistry.register_from_metadata(get_available_tools(), TOOL_METADATA)
        # Domain tools self-register independently. One optional dependency must
        # never prevent an unrelated domain from joining the canonical inventory.
        for domain_module in (
            "agent.voice_runtime",
            "agent.generation_runtime",
        ):
            try:
                __import__(domain_module)
            except Exception as domain_tools_exc:
                logger.debug("Domain tool registration skipped for {}: {}", domain_module, domain_tools_exc)

        # Load/migrate the canonical Connection authority before Skills. Skills
        # consume Connection capabilities; they never establish authentication.
        from agent.connections import get_connection_registry
        self._connection_registry = get_connection_registry()
        try:
            from agent.connection_lifecycle import get_connection_lifecycle_service

            get_connection_lifecycle_service().migrate_legacy_settings(config)
        except Exception as connection_migration_exc:
            logger.warning(
                "Legacy Connection migration failed closed: {}",
                connection_migration_exc,
            )

        # Skills may now register workflows over the current Connection
        # authority before configured MCP capabilities join the inventory.
        default_ws = getattr(config, "default_workspace", "").strip() or None
        self.configure_workspace(default_ws)

        # Load MCP dynamic tools via process-wide singleton (Trust Center + agent share state)
        self._mcp_manager = None
        try:
            from agent.mcp_client import get_mcp_manager

            mcp_mgr = get_mcp_manager()
            self._mcp_manager = mcp_mgr
            if getattr(config, "mcp_servers", None):
                mcp_mgr.initialize_servers(config.mcp_servers)
        except Exception as e:
            logger.warning(f"Failed to initialize MCP servers: {e}")

        # lc_tools = tools filtered by config safety gates
        # Wrap web_search so every canonical ToolRun uses the same grounding boundary.
        self.lc_tools = self._apply_authority_to_lc_tools(
            self._apply_search_grounding_to_lc_tools(ToolRegistry.get_config_filtered_funcs(config))
        )
        self.tools = self._create_tools()

        # Merge MCP tools into self.tools (StructuredTool or shim already registered)
        for name, entry in ToolRegistry._entries.items():
            if entry.category == "mcp" and not any(t.name == name for t in self.tools):
                func = entry.func
                if hasattr(func, "invoke") or hasattr(func, "name"):
                    # Prefer the LangChain tool object directly when possible
                    try:
                        self.tools.append(func)
                        continue
                    except Exception:
                        pass

                def make_wrapper(t_func=func):
                    def _wrapped(**kwargs):
                        if hasattr(t_func, "invoke"):
                            return t_func.invoke(kwargs)
                        return t_func(**kwargs)

                    return _wrapped

                self.tools.append(Tool(name, make_wrapper(func), entry.description))
        self.tools = [
            item if isinstance(item, AuthorityCheckedTool) else AuthorityCheckedTool(self, item)
            for item in self.tools
        ]
        # Ordinary Turns use only Turn Understanding + ModelExecutionControlPlane.
        # Initialize the intent router with tools and source context
        self._router = IntentRouter(
            tools=self.tools,
            lc_tools=self.lc_tools,
            source=getattr(self, "_current_source", None),
            config=config,
        )
        self._initializing_tool_inventory = False
        self._tool_inventory_snapshot = ToolRegistry.inventory_snapshot(config)
        logger.info(
            "Agent initialized inventory_revision={} inventory_sha256={} "
            "inventory_count={} runtime_tool_count={} provider={}",
            self._tool_inventory_snapshot.get("revision"),
            self._tool_inventory_snapshot.get("sha256"),
            self._tool_inventory_snapshot.get("count"),
            len(self.lc_tools),
            self.llm_provider.value,
        )

        # The API runtime coordinator is the sole scheduler/execution owner.
        # Individual agents cannot start competing background callback loops.
        self._routine_manager = None

        # Connect heartbeat scheduler (v5.4.0 — Proactive Mode)
        self._heartbeat_manager = None

        # Connect proactive engine (v6.1.0 — Autonomous Agent Mode)
        self._proactive_engine = None

    def _load_soul(self) -> str:
        """
        Load SOUL.md content if it exists and is enabled.

        The soul defines the agent's core identity, values, communication style,
        and boundaries. It is loaded once per session and injected into the
        system prompt BEFORE skills, giving it highest priority.

        Returns:
            str: Soul content or empty string if not found/disabled.
        """
        # Check if soul system is enabled
        soul_config = getattr(config, "soul", None)
        if soul_config is None:
            return ""
        if not getattr(soul_config, "enabled", True):
            logger.debug("SOUL.md system disabled via config")
            return ""

        # Get soul path from config
        soul_path_str = getattr(soul_config, "path", "./SOUL.md")
        soul_path = Path(soul_path_str).expanduser()
        
        # Packaged desktop defaults must resolve to durable app data rather than
        # PyInstaller's temporary extraction directory.
        if not soul_path.is_absolute():
            backend_dir = DATA_DIR if os.getenv("ECHOSPEAK_RUNTIME_KIND", "").strip().lower() == "desktop" else Path(__file__).parent.parent
            soul_path = backend_dir / soul_path
        
        # Check if file exists
        if not soul_path.exists():
            logger.debug(f"SOUL.md not found at {soul_path}")
            return ""

        max_chars = int(getattr(soul_config, "max_chars", 8000) or 8000)
        try:
            mtime_ns = soul_path.stat().st_mtime_ns
            cache = getattr(self, "_soul_cache", {}) or {}
            if (
                str(cache.get("path") or "") == str(soul_path)
                and int(cache.get("mtime_ns", -1)) == mtime_ns
                and int(cache.get("max_chars", 0)) == max_chars
            ):
                return str(cache.get("content") or "")
        except OSError:
            mtime_ns = -1
        
        # Read and validate content
        try:
            content = soul_path.read_text(encoding="utf-8").strip()
            if not content:
                logger.debug(f"SOUL.md is empty at {soul_path}")
                return ""

            # Apply character limit
            if len(content) > max_chars:
                logger.warning(
                    f"SOUL.md exceeds {max_chars} chars, truncating. "
                    f"Consider splitting into smaller sections."
                )
                content = content[:max_chars]

            self._soul_cache = {
                "path": str(soul_path),
                "mtime_ns": mtime_ns,
                "max_chars": max_chars,
                "content": content,
            }
            logger.info(f"Loaded SOUL.md from {soul_path} ({len(content)} chars)")
            return content

        except Exception as e:
            logger.warning(f"Failed to load SOUL.md: {e}")
            return ""

    def _clear_session_project_scope(
        self,
        *,
        thread_id: Optional[str] = None,
        reason: str = "Project detached",
        stop_preview: bool = True,
        clear_active_work: bool = True,
    ) -> None:
        """Authoritative clear of Project scope for one Session (detach / switch / delete).

        Clears ThreadSessionState path fields, pending approvals/retries, ActiveWork,
        request-local tool root, and optional preview process together.
        """
        tid = str(thread_id or self._thread_key() or "default").strip() or "default"
        prev = self._state_store.get_thread_state(tid)
        if prev.pending_approval_id:
            try:
                self._state_store.update_approval(
                    prev.pending_approval_id,
                    status="canceled",
                    outcome_summary=reason,
                )
            except Exception as exc:
                logger.debug("Could not cancel pending approval on scope clear: {}", exc)
        if self._thread_key() == tid:
            self._active_project_id = None
            self._pending_action = None
            self._active_approved_action = None
            try:
                from agent.tools import set_active_project_root, update_tool_execution_context

                set_active_project_root(None)
                update_tool_execution_context(project_root="", workspace_root="", active_project_id="")
            except Exception:
                pass
            try:
                if hasattr(self, "_last_local_project_path"):
                    self._last_local_project_path = None
            except Exception:
                pass
        self._state_store.update_thread_state(
            tid,
            active_project_id="",
            project_path="",
            workspace_root="",
            pending_approval_id="",
            pending_actions=[],
            retry_target={},
            objective="",
        )
        if clear_active_work:
            try:
                from agent.active_work import ActiveWorkStore

                ActiveWorkStore().clear(tid)
            except Exception as exc:
                logger.debug("ActiveWork clear failed for {}: {}", tid, exc)
        if stop_preview:
            try:
                from agent.project_preview import stop_preview_for_scope_change

                stop_preview_for_scope_change(
                    tid,
                    reason=reason,
                    detached_project_id=str(prev.active_project_id or ""),
                    state_store=self._state_store,
                )
            except Exception as exc:
                logger.debug("Preview stop failed for {}: {}", tid, exc)
        logger.info("Session project scope cleared thread={} reason={}", tid, reason)

    def activate_project(self, project_id: Optional[str]) -> bool:
        """Activate a project by ID (or deactivate by passing None).

        When active, the project's context_prompt is injected into the system prompt.
        Attach/switch/detach is one scope transaction (ThreadSessionState + ActiveWork
        + approvals + preview + tool root).

        Args:
            project_id: Project ID to activate, or None to deactivate.

        Returns:
            True if the project was found and activated (or deactivated).
        """
        if project_id is None:
            self._clear_session_project_scope(reason="Project deactivated")
            logger.info("Project deactivated")
            return True
        try:
            from agent.projects import get_project_manager
            pm = get_project_manager()
            project = pm.get_project(project_id)
            if project:
                previous_state = self._state_store.get_thread_state(self._thread_key())
                prev_id = str(previous_state.active_project_id or "").strip()
                next_id = str(project_id).strip()
                metadata = dict(project.metadata or {})
                project_path = str(
                    project.workspace_root
                    or metadata.get("project_path")
                    or metadata.get("workspace_root")
                    or metadata.get("path")
                    or ""
                ).strip()
                previous_path = str(previous_state.project_path or "").strip()
                try:
                    path_changed = bool(previous_path or project_path) and os.path.normcase(
                        os.path.abspath(previous_path)
                    ) != os.path.normcase(os.path.abspath(project_path))
                except (OSError, ValueError):
                    path_changed = previous_path != project_path
                scope_changed = prev_id != next_id or path_changed
                # ProjectManager owns Project identity/root. A real scope change
                # invalidates projected session authority; an idempotent refresh
                # must not discard approvals or retry state.
                if scope_changed and (prev_id or previous_path or previous_state.pending_approval_id):
                    self._clear_session_project_scope(
                        reason="Invalidated because the active Project changed",
                        stop_preview=True,
                        clear_active_work=True,
                    )
                self._active_project_id = project_id
                self._state_store.update_thread_state(
                    self._thread_key(),
                    active_project_id=project_id,
                    project_path=project_path,
                    workspace_root=project_path,
                )
                try:
                    from agent.tools import set_active_project_root

                    if project_path:
                        set_active_project_root(project_path)
                except Exception:
                    pass
                logger.info(f"Activated project: {project.name}")
                return True
            logger.warning(f"Project not found: {project_id}")
            return False
        except Exception as e:
            logger.warning(f"Failed to activate project: {e}")
            return False

    def _tool_policy_flags_satisfied(self, name: str) -> bool:
        from agent.tool_registry import ToolRegistry

        flags = ToolRegistry.get_permission_flags(name)
        for flag in flags:
            attr_name = str(flag or "").strip().lower()
            if attr_name and not bool(getattr(config, attr_name, False)):
                return False
        return True

    def _registered_tool_names(self) -> frozenset[str]:
        names = {
            str(getattr(tool, "name", "") or "").strip()
            for tool in [*(self.tools or []), *(self.lc_tools or [])]
        }
        return frozenset(name for name in names if name)

    def _is_local_filesystem_intent(self, user_input: str) -> bool:
        """User wants local Desktop/files/project inspection — NEVER web_search.

        Structural signals only (no hard-coded project names). Covers:
        scan/list/read/open folder, desktop paths, local project work.
        """
        text = self._extract_user_request_text(user_input or "")
        low = re.sub(r"\s+", " ", (text or "").lower().strip())
        if not low:
            return False
        # Explicit local filesystem signals
        if re.search(
            r"\b("
            r"on my desktop|my desktop|look on (?:my )?desktop|list (?:my )?desktop|"
            r"from (?:my )?desktop|on the desktop|desktop[/\\]|search (?:my )?desktop|"
            r"read the files?|read (?:the )?project|open (?:the )?(?:project|folder|files?)|"
            r"list (?:the )?files?|inspect (?:the )?(?:project|folder|code|files?)|"
            r"look at (?:the )?(?:files?|code|project|folder)|"
            r"scan (?:the )?(?:folder|project|files?|directory|code)|"
            r"go into (?:the )?(?:folder|project|directory)|"
            r"understand (?:the )?project|go through (?:the )?files?|"
            r"start (?:on )?(?:the )?(?:project|folder)|"
            r"file://|~/desktop"
            r")\b",
            low,
        ):
            return True
        # Desktop + any inspect/work verb
        if re.search(r"\bdesktop\b", low) and re.search(
            r"\b(scan|list|read|open|folder|project|files?|code|inspect|check|"
            r"look|start|work|together)\b",
            low,
        ):
            return True
        # "start 2d-shooter-game on my desktop" / "start on <slug> (desktop|folder)"
        if re.search(
            r"\b(?:start on|start|open|continue on|work on|scan)\s+[a-z0-9][\w.-]{1,48}\b",
            low,
        ) and re.search(r"\b(desktop|files?|folder|project|code|read|look|scan)\b", low):
            return True
        return False

    def _explicit_files_named_in_request(self, user_input: str, files: List[str]) -> List[str]:
        """Return only files explicitly named by the current request.

        Supporting reads, cached ActiveWork, and feature heuristics must never
        become mutation targets when the user supplied an exact basename.
        Explicit exclusions ("do not edit game.js") remove those basenames.
        """
        request = str(user_input or "").replace("\\", "/").casefold()
        excluded: set[str] = set()
        for m in re.finditer(
            r"(?i)\b(?:do\s+not|don't|dont|never|without)\s+(?:edit|change|modify|touch|write|update)\s+"
            r"(?:the\s+)?[`'\"]?([a-z0-9_.-]+\.[a-z0-9]+)[`'\"]?",
            str(user_input or ""),
        ):
            excluded.add(m.group(1).casefold())
        for m in re.finditer(
            r"(?i)\b(?:except|excluding|not)\s+[`'\"]?([a-z0-9_.-]+\.[a-z0-9]+)[`'\"]?",
            str(user_input or ""),
        ):
            excluded.add(m.group(1).casefold())
        named: List[str] = []
        for file_path in files:
            basename = Path(file_path).name.casefold()
            if not basename or basename in excluded:
                continue
            # \b allows "index.html." / "index.html," sentence punctuation after the name.
            if re.search(rf"(?<![\w/\\]){re.escape(basename)}\b", request):
                named.append(file_path)
        return named

    def _file_write_path_allowed_by_request(self, user_input: str, path: str, project_files: Optional[List[str]] = None) -> bool:
        """True when path is allowed as a mutation target for this request."""
        path_name = Path(str(path or "")).name.casefold()
        if not path_name:
            return False
        low = str(user_input or "").casefold()
        if re.search(
            rf"(?i)\b(?:do\s+not|don't|dont|never)\s+(?:edit|change|modify|touch|write|update)\s+"
            rf"(?:the\s+)?[`'\"]?{re.escape(path_name)}[`'\"]?",
            str(user_input or ""),
        ):
            return False
        files = list(project_files or [])
        if files:
            named = self._explicit_files_named_in_request(user_input, files)
            if named:
                allowed = {Path(f).name.casefold() for f in named}
                return path_name in allowed
        # No project file list: require basename mention if any filename is mentioned at all
        mentioned = re.findall(r"(?i)\b([a-z0-9_.-]+\.[a-z0-9]{1,8})\b", str(user_input or ""))
        if mentioned:
            names = {m.casefold() for m in mentioned}
            # If user named files and this path is among them
            if path_name in names:
                return True
            # Path not named but other files were — refuse silent retarget
            return False
        return True

    def _content_has_unresolved_edit_markers(self, content: str) -> bool:
        """True when body still contains raw SEARCH/REPLACE or conflict chrome."""
        body = str(content or "")
        if not body:
            return False
        if "<<<<<<< SEARCH" in body or ">>>>>>> REPLACE" in body:
            return True
        if "<<<<<<<" in body and "=======" in body and ">>>>>>>" in body:
            return True
        return False

    def _clamp_discord_casual_reply(self, user_input: str, response_text: str) -> str:
        """Keep casual Discord replies short and human-facing."""
        text = re.sub(r"\s+", " ", str(response_text or "")).strip()
        if not text:
            return text
        banned_fragments = [
            "since i'm an ai",
            "as an ai",
            "i don't have a personal life",
            "digital ether",
        ]
        parts = re.split(r"(?<=[.!?])\s+", text)
        kept: list[str] = []
        for part in parts:
            low = part.lower()
            if any(fragment in low for fragment in banned_fragments):
                continue
            kept.append(part.strip())
            if len(" ".join(kept)) >= 90:
                break
        out = " ".join([p for p in kept if p]).strip() or text
        if len(out) > 120:
            out = out[:117].rsplit(" ", 1)[0].rstrip(" ,;:") + "..."
        if out.count("?") > 1:
            first_q = out.find("?")
            out = out[: first_q + 1] + out[first_q + 1 :].replace("?", ".")
        return out

    def configure_workspace(self, workspace_id: Optional[str]) -> None:
        workspace_id = (workspace_id or "").strip()
        self._workspace_id = workspace_id or None
        skills_dir = Path(getattr(config, "skills_dir", "") or "").expanduser()
        workspaces_dir = Path(getattr(config, "workspaces_dir", "") or "").expanduser()
        try:
            skills_dir.mkdir(parents=True, exist_ok=True)
            workspaces_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        skills = load_skills(skills_dir)
        workspace = load_workspace(workspaces_dir, workspace_id) if workspace_id else None
        skill_defs = []
        if workspace is not None:
            for skill_id in workspace.skill_ids:
                skill = skills.get(skill_id)
                if skill is not None:
                    skill_defs.append(skill)
        self._active_skill_defs = list(skill_defs)
        self._skills_prompt = build_skills_prompt(skill_defs)
        self._workspace_prompt = (workspace.prompt if workspace else "").strip()
        self._workspace_name = (workspace.name if workspace else "").strip()

        # Skill → Tool Bridge: load custom tools from active skills
        skill_tool_names: list[str] = []
        inventory_revision_before = int(ToolRegistry.inventory_snapshot().get("revision") or 0)
        for skill_def in skill_defs:
            skill_path = skills_dir / skill_def.id
            new_tools = load_skill_tools(skill_path)
            skill_tool_names.extend(new_tools)
        inventory_changed = (
            int(ToolRegistry.inventory_snapshot().get("revision") or 0)
            != inventory_revision_before
        )
        # Rebuild only when the authoritative registry revision changed.
        if inventory_changed and not getattr(self, "_initializing_tool_inventory", False):
            self.lc_tools = self._apply_authority_to_lc_tools(
                self._apply_search_grounding_to_lc_tools(ToolRegistry.get_config_filtered_funcs(config))
            )
            self.tools = [
                item if isinstance(item, AuthorityCheckedTool) else AuthorityCheckedTool(self, item)
                for item in self._create_tools()
            ]
            self._router = IntentRouter(
                tools=self.tools,
                lc_tools=self.lc_tools,
                source=getattr(self, "_current_source", None),
                config=config,
            )
            self._tool_inventory_snapshot = ToolRegistry.inventory_snapshot(config)

        # Skill → Plugin Bridge: load pipeline plugins from active skills
        for skill_def in skill_defs:
            skill_path = skills_dir / skill_def.id
            load_skill_plugin(skill_path)

        # Skill/workspace TOOLS.txt are soft skill metadata and prompts only.
        # They must NOT hard-block the registered tool inventory (chat workspace
        # historically listed only calculate/get_system_time and hid Project tools).
        # Project scope + permissions remain the real execution gates.
        skill_allowlists = [s.tool_allowlist for s in skill_defs]
        if skill_tool_names:
            skill_allowlists.append(skill_tool_names)
        # Keep merge result for diagnostics only; do not install as a runtime ceiling.
        _ = merge_tool_allowlists(
            workspace.tool_allowlist if workspace else [],
            skill_allowlists,
        )
        self._tool_allowlist_override = None
        self._skills_fingerprint = self._compute_skills_fingerprint(skills_dir, workspaces_dir, workspace_id)
        self._state_store.update_thread_state(self._thread_key(), workspace_id=str(self._workspace_id or ""))

    def _compute_skills_fingerprint(self, skills_dir: Path, workspaces_dir: Path, workspace_id: str) -> str:
        h = hashlib.sha256()
        base_paths: list[Path] = []
        try:
            if skills_dir.exists():
                base_paths.append(skills_dir)
        except Exception:
            pass
        try:
            if workspaces_dir.exists() and workspace_id:
                ws = (workspaces_dir / workspace_id)
                if ws.exists():
                    base_paths.append(ws)
        except Exception:
            pass

        for base in base_paths:
            try:
                for p in sorted(base.rglob("*")):
                    try:
                        if not p.is_file():
                            continue
                        if p.name.startswith("."):
                            continue
                        if p.suffix.lower() not in {".md", ".txt", ".json"}:
                            continue
                        st = p.stat()
                        h.update(str(p).encode("utf-8", errors="ignore"))
                        h.update(str(int(st.st_mtime_ns)).encode("utf-8"))
                        h.update(str(int(st.st_size)).encode("utf-8"))
                    except Exception:
                        continue
            except Exception:
                continue
        return h.hexdigest()

    def _discord_server_assistant_tools(self) -> frozenset[str]:
        return frozenset({"web_search", "get_system_time", "calculate", "project_update_context"})

    def _limited_discord_server_tool_names(self, query_lower: str) -> frozenset[str]:
        low = (query_lower or "").strip().lower()
        if not low:
            return frozenset()
        if self._is_small_talk_query(low):
            return frozenset()
        if self._is_direct_time_question(low):
            return frozenset({"get_system_time"})
        has_calc_keyword = any(ind in low for ind in ["calculate", "compute", "evaluate", "solve", "times", "equals"])
        has_math_operator = bool(re.search(r"\d\s*[+\-*/^]\s*\d", low))
        if has_calc_keyword or has_math_operator:
            return frozenset({"calculate"})
        if self._is_schedule_time_query(low):
            return frozenset({"web_search"})
        if self._is_live_web_intent(low):
            return frozenset({"web_search"})
        if any(x in low for x in ["search", "look up", "find out", "news", "headlines", "current events", "weather", "latest"]):
            return frozenset({"web_search"})
        return frozenset()

    def _tool_allowed(self, name: str) -> bool:
        if not name:
            return False
        approved = self._approved_action_matches(name)
        retry_action = getattr(self, "_active_retry_action", None)
        retry_allowed = bool(
            isinstance(retry_action, dict)
            and str(retry_action.get("tool") or "") == name
            and name in set(retry_action.get("allowed_tool_names") or [])
        )
        decision = getattr(self, "_current_mode_decision", None)
        if decision is not None and not tool_allowed_by_mode(decision, name) and not approved and not retry_allowed:
            return False
        execution_context = getattr(self, "_execution_context", None)
        authority = getattr(self, "_turn_execution_authority", None)
        if bool(getattr(self, "_canonical_semantic_flow", False)) and authority is not None:
            context_allowed = set(authority.allowed_tool_names)
            constraints = set(authority.constraints)
        elif execution_context is not None:
            context_allowed = set(getattr(execution_context, "allowed_tool_names", []) or [])
            constraints = set(getattr(execution_context, "constraints", []) or [])
        else:
            context_allowed = set()
            constraints = set()
        if authority is not None or execution_context is not None:
            if name not in context_allowed and not approved and not retry_allowed:
                return False
            if name == "web_search" and "local_first" in constraints:
                details = dict(getattr(execution_context, "operation_details", {}) or {})
                locally_inspected = bool(
                    set(details.get("tools_used") or [])
                    & {"file_list", "file_read", "project_status", "project_update_context"}
                )
                if not locally_inspected:
                    return False
        if getattr(self, "_current_source", None) == "discord_bot" and name not in self._discord_server_assistant_tools():
            return False
        # Core inventory is not gated by skill-workspace mode (chat/research/coding).
        # Filesystem/terminal still require Project path roots + policy at invoke time.
        safe_baseline = {
            "web_search",
            "get_system_time",
            "calculate",
            "project_update_context",
            "project_status",
            "file_list",
            "file_read",
            "system_info",
        }
        if name in safe_baseline:
            return True
        project_tools = {
            "file_write",
            "file_mkdir",
            "file_delete",
            "file_move",
            "file_copy",
            "terminal_run",
            "artifact_write",
            "notepad_write",
        }
        if name in project_tools:
            return True
        allowlist = self._tool_allowlist_override
        if allowlist is None:
            return True
        return name in allowlist

    def _load_webhook_secret(self) -> str:
        secret = str(getattr(config, "webhook_secret", "") or "").strip()
        if secret:
            return secret
        path_val = str(getattr(config, "webhook_secret_path", "") or "").strip()
        if not path_val:
            return ""
        path = Path(path_val).expanduser()
        try:
            if path.exists():
                return path.read_text(encoding="utf-8").strip()
        except Exception:
            return ""
        return ""

    def get_doctor_report(self) -> Dict[str, Any]:
        if self.llm_provider == ModelProvider.OPENAI:
            provider_model = config.openai.model
        elif self.llm_provider == ModelProvider.GEMINI:
            provider_model = config.gemini.model
        else:
            provider_model = config.local.model_name
        provider_base_url = None
        if self.llm_provider not in (ModelProvider.OPENAI, ModelProvider.GEMINI, ModelProvider.LLAMA_CPP):
            provider_base_url = config.local.base_url

        provider_ok = True
        provider_notes: list[str] = []
        if self.llm_provider == ModelProvider.OPENAI:
            api_key = config.openai.api_key or os.getenv("OPENAI_API_KEY", "")
            if not api_key:
                provider_ok = False
                provider_notes.append("Missing OPENAI_API_KEY")
        elif self.llm_provider == ModelProvider.GEMINI:
            api_key = config.gemini.api_key or os.getenv("GEMINI_API_KEY", "")
            if not api_key:
                provider_ok = False
                provider_notes.append("Missing GEMINI_API_KEY")
        elif self.llm_provider not in (ModelProvider.LLAMA_CPP,):
            if not (provider_base_url or "").strip():
                provider_ok = False
                provider_notes.append("Missing base_url for local provider")

        memory_ok = bool(getattr(self.memory, "embeddings", None)) or not bool(getattr(self.memory, "use_faiss", True))
        docs_enabled = bool(getattr(config, "document_rag_enabled", False))
        docs_ok = not docs_enabled or self.document_store is not None

        cron_enabled = bool(getattr(config, "cron_enabled", False))
        cron_available = importlib.util.find_spec("croniter") is not None
        webhook_enabled = bool(getattr(config, "webhook_enabled", False))
        webhook_secret = self._load_webhook_secret()
        webhook_ok = not webhook_enabled or bool(webhook_secret)
        routine_webhook_count = 0
        try:
            from agent.routines import get_routine_manager

            routine_manager = get_routine_manager()
            routine_webhook_count = len([
                routine for routine in routine_manager.list_routines(enabled_only=True)
                if str(getattr(routine, "trigger_type", "") or "") == "webhook"
                and str(getattr(routine, "webhook_path", "") or "").strip()
            ])
        except Exception:
            routine_webhook_count = 0

        allowlist = sorted(self._tool_allowlist_override) if self._tool_allowlist_override else []
        file_root = str(getattr(config, "file_tool_root", "") or ".").strip() or "."
        term_deny = [
            str(x).strip().lower()
            for x in (getattr(config, "terminal_command_denylist", None) or [])
            if str(x).strip()
        ]
        discord_diag: Dict[str, Any] = {
            "enabled": bool(getattr(config, "allow_discord_bot", False)),
            "token_set": bool(getattr(config, "discord_bot_token", "")),
            "auto_confirm": bool(getattr(config, "discord_bot_auto_confirm", False)),
            "owner_id_set": bool(str(getattr(config, "discord_bot_owner_id", "") or "").strip()),
            "uses_shared_process_query": True,
            "wrapper_marker_supported": True,
            "last_source": str(getattr(self, "_current_source", "") or ""),
            "last_thread_id": str(getattr(self, "_current_thread_id", "") or ""),
            "last_user_role": str(getattr(self, "_current_user_role", "") or ""),
            "bot_running": False,
            "has_loop": False,
            "guild_count": 0,
        }
        try:
            from discord_bot import get_bot

            bot = get_bot()
            if bot is not None:
                try:
                    discord_diag["bot_running"] = bool(bot.is_running())
                except Exception:
                    discord_diag["bot_running"] = False
                discord_diag["has_loop"] = bool(getattr(bot, "_loop", None))
                client = getattr(bot, "client", None)
                guilds = list(getattr(client, "guilds", []) or []) if client is not None else []
                discord_diag["guild_count"] = len(guilds)
        except Exception as exc:
            discord_diag["error"] = str(exc)[:200]
        integrations_diag: Dict[str, Any] = {"discord": discord_diag}

        telegram_diag: Dict[str, Any] = {
            "enabled": bool(getattr(config, "allow_telegram_bot", False)),
            "token_set": bool(getattr(config, "telegram_bot_token", "")),
            "allowed_users_count": len(list(getattr(config, "telegram_allowed_users", []) or [])),
            "auto_confirm": bool(getattr(config, "telegram_auto_confirm", False)),
            "running": False,
        }
        try:
            from telegram_bot import get_telegram_bot

            tg = get_telegram_bot()
            telegram_diag["running"] = bool(tg and tg.is_running())
        except Exception as exc:
            telegram_diag["error"] = str(exc)[:200]
        integrations_diag["telegram"] = telegram_diag

        twitch_diag: Dict[str, Any] = {
            "enabled": bool(getattr(config, "allow_twitch", False)),
            "client_id_set": bool(getattr(config, "twitch_client_id", "")),
            "client_secret_set": bool(getattr(config, "twitch_client_secret", "")),
            "bot_token_set": bool(getattr(config, "twitch_bot_access_token", "")),
            "eventsub_secret_set": bool(getattr(config, "twitch_eventsub_secret", "")),
            "running": False,
        }
        try:
            import twitch_bot as _twitch_mod

            twi = getattr(_twitch_mod, "_twitch_bot", None)
            if twi is not None:
                twitch_diag["running"] = bool(twi.is_running())
        except Exception as exc:
            twitch_diag["error"] = str(exc)[:200]
        integrations_diag["twitch"] = twitch_diag

        twitter_diag: Dict[str, Any] = {
            "enabled": bool(getattr(config, "allow_twitter", False)),
            "bearer_token_set": bool(getattr(config, "twitter_bearer_token", "")),
            "access_token_set": bool(getattr(config, "twitter_access_token", "")),
            "access_token_secret_set": bool(getattr(config, "twitter_access_token_secret", "")),
            "running": False,
        }
        try:
            import twitter_bot as _twitter_mod

            tw = getattr(_twitter_mod, "_twitter_bot", None)
            if tw is not None:
                twitter_diag["running"] = bool(tw.is_running())
        except Exception as exc:
            twitter_diag["error"] = str(exc)[:200]
        integrations_diag["twitter"] = twitter_diag
        issues: list[str] = []
        if not provider_ok:
            issues.append("provider")
        if not memory_ok:
            issues.append("memory")
        if not docs_ok:
            issues.append("documents")
        if cron_enabled and not cron_available:
            issues.append("croniter")
        if not webhook_ok:
            issues.append("webhook_secret")
        if routine_webhook_count > 0 and not webhook_secret:
            issues.append("routine_webhooks_unsigned")
        if discord_diag["enabled"] and discord_diag["token_set"] and not discord_diag["bot_running"]:
            issues.append("discord_bot")
        if telegram_diag["enabled"] and telegram_diag["token_set"] and not telegram_diag["running"]:
            issues.append("telegram_bot")
        if twitch_diag["enabled"] and not (twitch_diag["client_id_set"] and twitch_diag["client_secret_set"]):
            issues.append("twitch_config")
        if twitter_diag["enabled"] and not (twitter_diag["bearer_token_set"] or twitter_diag["access_token_set"]):
            issues.append("twitter_config")

        session_memory_diag: Dict[str, Any] = {"enabled": bool(getattr(config, "session_memory_enabled", True))}
        try:
            if session_memory_diag["enabled"]:
                session_memory_diag = self._session_memory.doctor(self._current_thread_id or "default")
        except Exception as exc:
            session_memory_diag["error"] = str(exc)[:200]

        reliability_diag: Dict[str, Any] = {
            "search_grounding": {
                "enabled": bool(getattr(config, "search_grounding_enabled", True)),
                "max_candidates": int(getattr(config, "search_grounding_max_candidates", 3) or 3),
                "last": getattr(self, "_last_grounded_search_result", None),
            },
            "context_budget": {
                "enabled": bool(getattr(config, "context_budget_enabled", True)),
                "last": getattr(self, "_last_context_budget_report", None),
            },
            "session_memory": session_memory_diag,
            "verification": self._verification_telemetry.report() if getattr(self, "_verification_telemetry", None) is not None else {},
        }

        return {
            "ok": len(issues) == 0,
            "issues": issues,
            "provider": {
                "id": self.llm_provider.value,
                "model": provider_model,
                "base_url": provider_base_url,
                "ok": provider_ok,
                "notes": provider_notes,
            },
            "memory": {
                "path": str(getattr(self.memory, "memory_path", "")),
                "use_faiss": bool(getattr(self.memory, "use_faiss", False)),
                "file_memory_enabled": bool(getattr(self.memory, "file_memory_enabled", False)),
                "ok": memory_ok,
            },
            "documents": {
                "enabled": docs_enabled,
                "ok": docs_ok,
            },
            "workspace": self.project_scope_report(),
            "tools": {
                "count": len(self.lc_tools or []),
                "allowlist": allowlist,
            },
            "tool_calling": self._tool_calling_diagnostics(),
            "discord": discord_diag,
            "integrations": integrations_diag,
            "reliability": reliability_diag,
            "features": {
                "action_parser_enabled": bool(getattr(config, "action_parser_enabled", True)),
                "system_actions": bool(getattr(config, "enable_system_actions", False)),
                "allow_file_write": bool(getattr(config, "allow_file_write", False)),
                "allow_terminal_commands": bool(getattr(config, "allow_terminal_commands", False)),
                "terminal_denylist": term_deny,
                "file_tool_root": file_root,
                "cron_enabled": cron_enabled,
                "croniter_available": cron_available,
                "webhook_enabled": webhook_enabled,
                "webhook_secret_set": bool(webhook_secret),
                "routine_webhook_count": routine_webhook_count,
                "routine_webhooks_signed": bool(webhook_secret),
            },
        }

    def _format_doctor_report(self, report: Dict[str, Any]) -> str:
        status = "OK" if report.get("ok") else "CHECK"
        lines = [f"Doctor report ({status})"]
        provider = report.get("provider") or {}
        prov_line = f"Provider: {provider.get('id')} ({provider.get('model')})"
        if not provider.get("ok"):
            prov_line += " [check]"
        lines.append(prov_line)
        for note in provider.get("notes") or []:
            lines.append(f"  - {note}")

        memory = report.get("memory") or {}
        mem_line = "OK" if memory.get("ok") else "CHECK"
        lines.append(
            f"Memory: {mem_line} (faiss={memory.get('use_faiss')}, file={memory.get('file_memory_enabled')})"
        )

        docs = report.get("documents") or {}
        docs_line = "OK" if docs.get("ok") else "CHECK"
        lines.append(f"Docs: {docs_line} (enabled={docs.get('enabled')})")

        workspace = report.get("workspace") or {}
        lines.append(f"Workspace: {workspace.get('id') or 'none'}")

        tools = report.get("tools") or {}
        lines.append(f"Tools: {tools.get('count', 0)} available")

        tool_calling = report.get("tool_calling") or {}
        if tool_calling:
            lines.append(
                "Tool calling: "
                + f"native={tool_calling.get('native_tool_calling_enabled')} "
                + f"mode={tool_calling.get('last_tool_calling_mode') or 'unknown'} "
                + f"stage4={tool_calling.get('last_stage4_branch') or 'none'}"
            )

        discord = report.get("discord") or {}
        if discord:
            lines.append(
                "Discord bot: "
                + f"enabled={discord.get('enabled')} "
                + f"running={discord.get('bot_running')} "
                + f"shared_core={discord.get('uses_shared_process_query')}"
            )

        integrations = report.get("integrations") or {}
        if isinstance(integrations, dict) and integrations:
            parts: list[str] = []
            for name in ("telegram", "twitch", "twitter"):
                item = integrations.get(name) or {}
                if isinstance(item, dict):
                    parts.append(f"{name}=enabled:{item.get('enabled')} running:{item.get('running')}")
            if parts:
                lines.append("Integrations: " + " | ".join(parts))

        reliability = report.get("reliability") or {}
        if isinstance(reliability, dict) and reliability:
            sg = reliability.get("search_grounding") or {}
            cb = reliability.get("context_budget") or {}
            sm = reliability.get("session_memory") or {}
            vt = reliability.get("verification") or {}
            lines.append(
                "Reliability: "
                + f"search_grounding={sg.get('enabled')} "
                + f"context_budget={cb.get('enabled')} "
                + f"session_memory={sm.get('enabled')} "
                + f"verification_events={vt.get('count', 0)}"
            )

        features = report.get("features") or {}
        lines.append(
            f"Action Parser: {'enabled' if features.get('action_parser_enabled') else 'disabled'}"
        )
        sa = "enabled" if features.get("system_actions") else "disabled"
        lines.append(
            "System actions: "
            + sa
            + f" (file_write={features.get('allow_file_write')}, terminal={features.get('allow_terminal_commands')})"
        )
        lines.append(f"FILE_TOOL_ROOT: {features.get('file_tool_root')}")
        term_deny = features.get("terminal_denylist") or []
        if isinstance(term_deny, list):
            lines.append(
                "TERMINAL_COMMAND_DENYLIST: "
                + (", ".join(term_deny) if term_deny else "(empty)")
            )
        cron_line = "enabled" if features.get("cron_enabled") else "disabled"
        cron_check = "ok" if features.get("croniter_available") else "missing"
        lines.append(f"Cron: {cron_line} (croniter={cron_check})")
        webhook_line = "enabled" if features.get("webhook_enabled") else "disabled"
        webhook_check = "set" if features.get("webhook_secret_set") else "missing"
        lines.append(f"Webhook: {webhook_line} (secret={webhook_check})")
        if int(features.get("routine_webhook_count") or 0) > 0:
            lines.append(
                f"Routine webhooks: {features.get('routine_webhook_count')} "
                + f"(signed={features.get('routine_webhooks_signed')})"
            )

        if report.get("issues"):
            lines.append("Issues: " + ", ".join(report["issues"]))
        return "\n".join(lines)

    def format_doctor_report(self, report: Optional[Dict[str, Any]] = None) -> str:
        if report is None:
            report = self.get_doctor_report()
        return self._format_doctor_report(report)

    def _create_tools(self) -> List[Tool]:
        from agent.tools import (
            analyze_screen,
            vision_qa,
            get_system_time,
            calculate,
            open_chrome,
            open_application,
            notepad_write,
            project_update_context,
            todo_manage,
            youtube_transcript,
            browse_task,
            discord_web_read_recent,
            discord_web_send,
            discord_contacts_add,
            discord_contacts_discover,
            discord_read_channel,
            discord_send_channel,
            system_info,
            desktop_list_windows,
            desktop_find_control,
            desktop_click,
            desktop_type_text,
            desktop_activate_window,
            desktop_send_hotkey,
            file_list,
            file_read,
            file_write,
            file_move,
            file_copy,
            file_delete,
            file_mkdir,
            artifact_write,
            terminal_run,
            project_status,
        )

        tools = [
            Tool(
                "web_search",
                lambda q, _agent=self: _agent._grounded_web_search(
                    q,
                    original_request=str(
                        getattr(_agent, "_active_user_query", None) or q or ""
                    ),
                    callbacks=getattr(_agent, "_current_callbacks", None),
                    # The canonical durable ToolRun owns the visible row when set.
                    emit_tool_events=True,
                ),
                "Search the web for information (evidence-grounded)",
            ),
            Tool("get_system_time", lambda: get_system_time.invoke({}), "Get current system time"),
            Tool("calculate", lambda expression: calculate.invoke({"expression": expression}), "Perform mathematical calculations"),
            Tool("system_info", lambda: system_info.invoke({}), "Get basic OS/CPU/GPU/RAM info"),
            Tool("youtube_transcript", lambda url, language=None: youtube_transcript.invoke({"url": url, "language": language} if language else {"url": url}), "Fetch a YouTube video's transcript"),
            Tool("browse_task", lambda url, task=None: browse_task.invoke({"url": url, "task": task} if task else {"url": url}), "Browse a website (opt-in system action)"),
            Tool(
                "discord_web_read_recent",
                lambda **k: discord_web_read_recent.invoke(k),
                "Read recent Discord messages via Playwright (requires a logged-in browser profile)",
            ),
            Tool(
                "discord_web_send",
                lambda **k: discord_web_send.invoke(k),
                "Send a Discord message via Playwright (opt-in system action)",
            ),
            Tool(
                "discord_contacts_add",
                lambda **k: discord_contacts_add.invoke(k),
                "Add/update a Discord contact mapping (opt-in system action)",
            ),
            Tool(
                "discord_contacts_discover",
                lambda **k: discord_contacts_discover.invoke(k),
                "Discover a Discord contact via Playwright (opt-in system action)",
            ),
            Tool(
                "discord_read_channel",
                lambda **k: discord_read_channel.invoke(k),
                "Read recent messages from a Discord server channel via bot (requires ALLOW_DISCORD_BOT=true)",
            ),
            Tool(
                "discord_send_channel",
                lambda **k: discord_send_channel.invoke(k),
                "Send a message to a Discord server channel via bot (requires ALLOW_DISCORD_BOT=true; confirmation-gated)",
            ),
            Tool("desktop_list_windows", lambda **k: desktop_list_windows.invoke(k), "List open desktop windows (Windows)"),
            Tool("desktop_find_control", lambda **k: desktop_find_control.invoke(k), "Find UI controls in a desktop window (Windows)"),
            Tool("desktop_click", lambda **k: desktop_click.invoke(k), "Click a UI control (opt-in system action)"),
            Tool("desktop_type_text", lambda **k: desktop_type_text.invoke(k), "Type text into a UI control (opt-in system action)"),
            Tool("desktop_activate_window", lambda **k: desktop_activate_window.invoke(k), "Activate a window (opt-in system action)"),
            Tool("desktop_send_hotkey", lambda **k: desktop_send_hotkey.invoke(k), "Send a hotkey (opt-in system action)"),
            Tool("file_list", lambda **k: file_list.invoke(k), "List files within a directory"),
            Tool("file_read", lambda **k: file_read.invoke(k), "Read a text file"),
            Tool("file_write", lambda **k: file_write.invoke(k), "Write text to a file (opt-in system action)"),
            Tool("file_move", lambda **k: file_move.invoke(k), "Move a file/folder (opt-in system action)"),
            Tool("file_copy", lambda **k: file_copy.invoke(k), "Copy a file/folder (opt-in system action)"),
            Tool("file_delete", lambda **k: file_delete.invoke(k), "Delete a file/folder (opt-in system action)"),
            Tool("file_mkdir", lambda **k: file_mkdir.invoke(k), "Create a folder (opt-in system action)"),
            Tool(
                "artifact_write",
                lambda filename=None, content="": artifact_write.invoke({"filename": filename, "content": content}),
                "Write text to a safe artifacts folder and return the file path",
            ),
            Tool("analyze_screen", lambda c="": analyze_screen.invoke({"context": c}), "Analyze screen content with OCR"),
            Tool("vision_qa", lambda q: vision_qa.invoke({"question": q}), "Answer questions about the current screen using a vision-language model"),
            Tool(
                "open_chrome",
                lambda url=None: open_chrome.invoke({"url": url}) if url else open_chrome.invoke({}),
                "Open Google Chrome (opt-in system action)",
            ),
            Tool(
                "open_application",
                lambda app, args=None: open_application.invoke({"app": app, "args": args} if args else {"app": app}),
                "Open/launch an application (opt-in system action; allowlisted)",
            ),
            Tool(
                "notepad_write",
                lambda content, filename=None: notepad_write.invoke({"content": content, "filename": filename} if filename else {"content": content}),
                "Open Notepad, type text, and save an artifact copy (opt-in system action)",
            ),
            Tool("terminal_run", lambda **k: terminal_run.invoke(k), "Run a terminal command (opt-in system action)"),
            Tool(
                "project_status",
                lambda **k: project_status.invoke(k or {}),
                "Inspect attached Project health (git, layout). Read-only; requires Project scope at execution.",
            ),
            Tool("project_update_context", lambda **k: project_update_context.invoke(k or {}), "Get latest project updates, changelog, recent commits"),
            Tool("todo_manage", lambda **k: todo_manage.invoke(k), "Manage the shared todo list (actions: list, add, update, delete). Visible in the Web UI."),
        ]
        return tools

    def _preferred_web_research_tool(self) -> Optional[Tool]:
        tool = next((t for t in self.tools if t.name == "web_search"), None)
        if tool is not None and self._tool_allowed(tool.name):
            return tool
        return None

    def _is_small_talk_query(self, query_lower: str) -> bool:
        q = re.sub(r"\s+", " ", str(query_lower or "").strip().lower())
        if not q:
            return False
        patterns = [
            r"^(?:yo|hi|hey|hello|sup|what(?:'s|s) up|good morning|good night|later|bye|goodbye|cya|gn|night)\s*[!.?]*$",
            r"^what(?:\s+are|\s*'re|\s*re)?\s+you\s+up\s+to(?:\s+today)?\s*[!.?]*$",
            r"^what(?:\s+are|\s*'re|\s*re)?\s+you\s+doing(?:\s+today)?\s*[!.?]*$",
            r"^wyd(?:\s+today)?\s*[!.?]*$",
        ]
        return any(re.fullmatch(pattern, q) is not None for pattern in patterns)

    def _has_live_info_subject(self, query_lower: str) -> bool:
        """True for web-fresh topics. Word-boundary only — never 'eth' inside 'together'."""
        q = re.sub(r"\s+", " ", str(query_lower or "").strip().lower())
        if not q:
            return False
        # Multi-word phrases first
        if any(
            p in q
            for p in (
                "exchange rate",
                "flight status",
                "is it open",
                "current events",
                "top stories",
                "breaking news",
                "latest news",
                "recent news",
                "sports odds",
                "betting odds",
            )
        ):
            return True
        # Short tokens MUST use word boundaries (eth⊂together was forcing web search on desktop turns)
        return bool(
            re.search(
                r"\b("
                r"weather|forecast|score|scores|price|stock|stocks|"
                r"bitcoin|btc|ethereum|eth|traffic|availability|released|"
                r"news|headlines|odds"
                r")\b",
                q,
            )
        )

    def _is_live_web_intent(self, query_lower: str) -> bool:
        q = re.sub(r"\s+", " ", str(query_lower or "").strip().lower())
        if not q:
            return False
        if self._is_small_talk_query(q):
            return False
        # Guard: only match live-web triggers if the query looks like a
        # question or request, NOT a purely conversational statement.
        # This prevents false positives like "im talking to you right now".
        has_question_signal = any(w in q for w in [
            "?", "what", "how", "when", "where", "who", "which",
            "is there", "show me", "tell me", "find", "search",
            "look up", "check", "get me", "give me", "research",
            "i wonder", "wondering", "come out", "coming out", "release",
        ])
        if not has_question_signal:
            return False

        # Phrase triggers (safe as substring)
        phrase_triggers = [
            "right now",
            "currently",
            "live score",
            "exchange rate",
            "flight status",
            "is it open",
            "last night",
            "last game",
            "sports odds",
            "betting odds",
            "come out",
            "coming out",
            "release date",
        ]
        if any(t in q for t in phrase_triggers):
            return True

        # Word-boundary triggers — avoid "won" matching "wonder", "live" in unrelated words, etc.
        word_triggers = (
            r"\blive\b",
            r"\bscore\b",
            r"\bscores\b",
            r"\bweather\b",
            r"\bforecast\b",
            r"\bprice\b",
            r"\bstock\b",
            r"\bstocks\b",
            r"\bbitcoin\b",
            r"\bbtc\b",
            r"\bethereum\b",
            r"\beth\b",
            r"\btraffic\b",
            r"\bavailability\b",
            r"\breleased\b",
            r"\brelease\b",
            r"\byesterday\b",
            r"\bwon\b",
            r"\blost\b",
            r"\bbeat\b",
            r"\bdefeated\b",
            r"\bstandings\b",
            r"\bplayoff\b",
            r"\bplayoffs\b",
            r"\bodds\b",
            r"\btrailer\b",
        )
        if any(re.search(p, q) for p in word_triggers):
            return True
        if re.search(r"\btoday\b", q) and self._has_live_info_subject(q):
            return True
        # Near-future sports slates (tomorrow used to fall through as non-live)
        if re.search(r"\b(today|tonight|tomorrow|this weekend)\b", q) and (
            self._has_schedule_terms(q)
            or re.search(r"\bwho(?:'s| is)?\s+playing\b", q)
            or re.search(r"\b(world cup|fifa|nhl|nba|nfl|mlb|match|fixture)\b", q)
        ):
            return True
        if re.search(r"\blatest\b", q) or re.search(r"\bbreaking\b", q):
            return True
        # Subject-anchored clarifiers already rewritten (kickoff timezone, price in CAD, …)
        if re.search(r"\b(timezone|time zone|kickoff|convert local|price in cad|price in usd)\b", q):
            return True
        if self._is_deeper_search_followup(q):
            return True
        return False

    # ── Universal Task Continuation engine ────────────────────────────────

    def _topic_template_from_subject(self, subject: str) -> str:
        """Normalize subject into a reusable topic skeleton (e.g. weather query)."""
        s = re.sub(r"\s+", " ", str(subject or "").strip())
        if not s:
            return ""
        low = s.lower()
        # Prefer weather skeleton so location swaps stay on-topic
        if any(w in low for w in ("weather", "forecast", "temperature", "temp")):
            return "weather"
        if any(w in low for w in ("score", "match", "game", "fifa", "nhl", "nba", "nfl", "kickoff", "fixture")):
            return "sports"
        if any(w in low for w in ("bitcoin", "btc", "price", "stock", "crypto", "ethereum", "usd", "cad")):
            return "finance"
        if any(w in low for w in ("trailer", "pre-order", "preorder", "box office", "dlc", "release date")):
            return "entertainment"
        if any(w in low for w in ("news", "headline", "breaking")):
            return "news"
        return "general"

    def _is_deeper_search_followup(self, query_text: str) -> bool:
        """User wants another / deeper web pass on the current subject."""
        q = re.sub(r"\s+", " ", str(query_text or "").strip().lower())
        q = q.replace("\u2019", "'").replace("\u2018", "'")
        if not q:
            return False
        exact = {
            "do a deeper search",
            "deeper search",
            "search deeper",
            "research deeper",
            "go deeper",
            "go further",
            "dig deeper",
            "look into it more",
            "look into that",
            "check more",
            "search more",
            "more search",
            "do another search",
            "search again",
        }
        if q in exact or q.rstrip("?.!") in exact:
            return True
        if re.search(
            r"\b(?:do\s+a\s+)?(?:deep(?:er)?|another)\s+(?:web\s+)?search\b",
            q,
        ):
            return True
        if re.search(r"\b(?:dig|go)\s+deeper\b", q) and len(q.split()) <= 8:
            return True
        if re.search(r"\b(?:search|research)\s+deeper\b", q):
            return True
        return False

    def _extract_answer_anchor_facts(self, response_text: str) -> str:
        """Pull matchups / kickoff times / prices from the answer into subject continuity.

        Live: answer said 'France vs Morocco … 4 p.m.' but subject stayed the broad
        FIFA slate query — timezone follow-ups then re-searched a different slate.
        """
        text = str(response_text or "")
        if not text or len(text) < 8:
            return ""
        bits: list[str] = []
        seen: set[str] = set()
        for m in re.finditer(
            r"\b([A-Z][a-zA-Z]{2,}(?:\s+[A-Z][a-zA-Z]{2,})?)\s+vs\.?\s+"
            r"([A-Z][a-zA-Z]{2,}(?:\s+[A-Z][a-zA-Z]{2,})?)\b",
            text,
        ):
            pair = f"{m.group(1)} vs {m.group(2)}"
            key = pair.lower()
            if key not in seen:
                seen.add(key)
                bits.append(pair)
            if len(bits) >= 3:
                break
        for m in re.finditer(
            r"\b(\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?|am|pm))\b",
            text,
            flags=re.IGNORECASE,
        ):
            t = re.sub(r"\s+", "", m.group(1).lower()).replace("a.m.", "am").replace("p.m.", "pm")
            # normalize display lightly
            disp = m.group(1).strip()
            if disp.lower() not in seen:
                seen.add(disp.lower())
                bits.append(disp)
            break  # one kickoff clock is enough for follow-ups
        # Stated price anchors (for currency follow-ups)
        pm = re.search(r"\$\s?([\d,]+(?:\.\d{1,2})?)", text)
        if pm and len(bits) < 4:
            bits.append(f"${pm.group(1).replace(',', '')}")
        return " ".join(bits[:4]).strip()

    def _has_schedule_terms(self, query_lower: str) -> bool:
        q = (query_lower or "").strip()
        if not q:
            return False
        schedule_terms = [
            "game",
            "match",
            "fixture",
            "schedule",
            "event",
            "concert",
            "show",
            "episode",
            "season",
            "flight",
            "departure",
            "arrival",
            "release",
            "launch",
            "play",
            "plays",
        ]
        return any(term in q for term in schedule_terms)

    def _is_next_upcoming_schedule_query(self, query_lower: str) -> bool:
        q = (query_lower or "").strip()
        if not q:
            return False
        if not any(t in q for t in ["next", "upcoming"]):
            return False
        return self._has_schedule_terms(q)

    @staticmethod
    def _search_query_fingerprint(query: str) -> str:
        """Collapse near-duplicate queries (word-order noise on mnt/et/convert)."""
        toks = re.findall(r"[a-z0-9]+", str(query or "").lower())
        # Drop pure ordering noise / stopwords that churn retries
        drop = {
            "the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "at",
            "each", "full", "list", "with", "please", "make", "sure", "its",
        }
        # Keep tz tokens but sort so "mnt et convert" == "et mnt convert"
        kept = [t for t in toks if t not in drop and len(t) > 1]
        return " ".join(sorted(set(kept)))

    def _scoped_search_fingerprint(
        self,
        query: str,
        *,
        task_run_id: str,
        requirement_id: str,
        freshness_class: str,
        tool_provider: str = "web_search",
    ) -> str:
        """Bind anti-loop identity to one requirement, never the whole turn."""

        semantic_query = self._search_query_fingerprint(query)
        if not semantic_query:
            return ""
        identity = {
            "task_run_id": str(task_run_id or ""),
            "requirement_id": str(requirement_id or ""),
            "query": semantic_query,
            "freshness_class": str(freshness_class or "unspecified"),
            "tool_provider": str(tool_provider or "web_search"),
        }
        return hashlib.sha256(
            json.dumps(
                identity, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()

    def _outer_web_search_scope_key(self) -> str:
        """Request/execution-local key so concurrent Sessions never share outer IDs."""
        return str(
            getattr(self, "_current_execution_id", "")
            or getattr(self, "_current_request_id", "")
            or ""
        ).strip() or "_default"

    def _set_outer_web_search_id(self, run_id: str) -> None:
        rid = str(run_id or "").strip()
        key = self._outer_web_search_scope_key()
        if not hasattr(self, "_lc_outer_web_search_by_exec") or self._lc_outer_web_search_by_exec is None:
            self._lc_outer_web_search_by_exec = {}
        if rid:
            self._lc_outer_web_search_by_exec[key] = rid
        # Compat mirror for StreamingHandler / existing callers (same request only).
        self._lc_outer_web_search_id = rid
        self._grounded_fanout_count = 0

    def _get_outer_web_search_id(self) -> str:
        key = self._outer_web_search_scope_key()
        m = getattr(self, "_lc_outer_web_search_by_exec", None) or {}
        scoped = str(m.get(key) or "").strip()
        if scoped:
            return scoped
        return str(getattr(self, "_lc_outer_web_search_id", "") or "").strip()

    def _clear_outer_web_search_id(self, run_id: str = "") -> None:
        key = self._outer_web_search_scope_key()
        m = getattr(self, "_lc_outer_web_search_by_exec", None)
        if isinstance(m, dict):
            cur = str(m.get(key) or "").strip()
            if not run_id or cur == str(run_id).strip() or not cur:
                m.pop(key, None)
        if not run_id or str(getattr(self, "_lc_outer_web_search_id", "") or "") == str(run_id).strip():
            self._lc_outer_web_search_id = ""

    def _raw_web_search_execute(self, query: str) -> str:
        """Execute the legacy provider-cascade callback.

        Noncanonical SearchGrounder callers use it as their provider callback so candidate
        loops never re-enter _grounded_web_search. Request-scoped cache avoids
        same query 3× for multi-candidate / multi-intent turns.
        """
        from agent.tools import web_search as raw_web_search

        q = str(query or "").strip()
        if not q:
            return ""
        cache = getattr(self, "_request_search_cache", None)
        # Exact + fingerprint keys (prevents mnt/et word-order re-fetches)
        cache_key = re.sub(r"\s+", " ", q).strip().lower()
        fp = self._search_query_fingerprint(q)
        if isinstance(cache, dict):
            if cache_key in cache:
                return str(cache[cache_key] or "")
            if fp and f"fp:{fp}" in cache:
                return str(cache[f"fp:{fp}"] or "")
        try:
            result = str(raw_web_search.invoke({"query": q}) or "")
        except TypeError:
            try:
                result = str(raw_web_search.invoke(q) or "")
            except Exception as exc:
                logger.warning(f"raw web_search failed: {exc}")
                result = ""
        except Exception as exc:
            logger.warning(f"raw web_search failed: {exc}")
            result = ""
        if isinstance(cache, dict) and cache_key:
            cache[cache_key] = result
            if fp:
                cache[f"fp:{fp}"] = result
        return result

    def _canonical_web_search_execute(self, query: str, *, strategy: str = "") -> tuple[str, str]:
        """Execute one provider-attributable acquisition for one TaskRun attempt."""

        from agent.web_search_providers import (
            format_hits_for_tool,
            resolve_provider_order,
            run_web_search_attempt,
        )

        provider_order = resolve_provider_order(config)
        if not provider_order:
            return (
                "[WEB_SEARCH]\nexecution_status=error\n"
                "result_state=provider_unavailable\nretryable=true\n"
                "No public search provider is configured.",
                "none",
            )
        strategy_name = str(strategy or "").strip().casefold()
        provider_index = 1 if strategy_name == "alternate_provider" and len(provider_order) > 1 else 0
        provider_name = provider_order[provider_index]
        result = run_web_search_attempt(
            query,
            provider_name=provider_name,
            config=config,
            max_hits=int(getattr(config, "web_search_max_results", 8) or 8),
        )
        provider_name = str(result.provider or provider_name or "none")
        if result.hits:
            body = format_hits_for_tool(result, multi_query=len(result.queries_used) > 1)
            return (
                "[WEB_SEARCH]\nexecution_status=success\nresult_state=data_found\n"
                f"provider={provider_name}\n{body}",
                provider_name,
            )
        detail = str((result.errors or ["No search results found."])[0])
        if result.errors:
            return (
                "[WEB_SEARCH]\nexecution_status=error\n"
                "result_state=provider_unavailable\nretryable=true\n"
                f"provider={provider_name}\n{detail}",
                provider_name,
            )
        return (
            "[WEB_SEARCH]\nexecution_status=success\n"
            "result_state=no_data\nretryable=true\n"
            f"provider={provider_name}\n{detail}",
            provider_name,
        )

    def _apply_search_grounding_to_lc_tools(self, tools: Optional[list]) -> list:
        """Wrap web_search StructuredTools so native tool-calling hits grounding."""
        out: list = []
        for tool in tools or []:
            name = str(getattr(tool, "name", "") or "")
            if name == "web_search":
                out.append(self._make_grounded_web_search_lc_tool(tool))
            else:
                out.append(tool)
        return out

    def _apply_authority_to_lc_tools(self, tools: Optional[list]) -> list:
        """Wrap registry tools in the canonical request-time authority boundary."""
        out: list = []
        try:
            from langchain_core.tools import StructuredTool
        except ImportError:
            try:
                from langchain.tools import StructuredTool  # type: ignore
            except ImportError:
                StructuredTool = None  # type: ignore
        for original in tools or []:
            if getattr(original, "_echo_authority_checked", False):
                out.append(original)
                continue
            if StructuredTool is None:
                out.append(AuthorityCheckedTool(self, original))
                continue
            name = str(getattr(original, "name", "") or "").strip()
            description = str(getattr(original, "description", "") or f"Run {name}")
            schema = getattr(original, "args_schema", None)

            def _make_run(raw: Any):
                def _run(**kwargs: Any) -> str:
                    return self._invoke_authorized_raw_tool(raw, kwargs).user_text()

                return _run

            try:
                wrapped = StructuredTool.from_function(
                    func=_make_run(original),
                    name=name,
                    description=description,
                    args_schema=schema,
                )
                out.append(wrapped)
            except Exception as exc:
                logger.warning("Failed to authority-wrap tool {}: {}", name, exc)
                # Do not expose an unguarded fallback tool.
        return out

    def _make_grounded_web_search_lc_tool(self, original: Any) -> Any:
        """Build a LangChain tool that routes through the search acquisition boundary."""
        agent = self
        description = (
            "Search public sources for ranked discovery results. The runtime evaluates "
            "whether the returned evidence satisfies the active requirement."
        )

        def _run(query: str = "", **kwargs: Any) -> str:

            q = str(query or kwargs.get("query") or kwargs.get("q") or "").strip()
            # Prefer the full user turn for multi-intent split (never trust model arg alone).
            orig = str(
                getattr(agent, "_active_user_query", None)
                or getattr(agent, "_last_user_input_for_plan", None)
                or q
            ).strip()
            # Full user turn is authoritative for multi-intent; always emit tool rows
            # so each sub-search is visible in chat (weather + FIFA both show up).
            return agent._grounded_web_search(
                q,
                original_request=orig or q,
                callbacks=getattr(agent, "_current_callbacks", None),
                emit_tool_events=True,
            )

        try:
            from langchain_core.tools import StructuredTool
        except ImportError:
            try:
                from langchain.tools import StructuredTool  # type: ignore
            except ImportError:
                # Fallback: monkey-patch invoke on a thin wrapper object.
                class _GroundedWebSearchTool:
                    name = "web_search"
                    description = description

                    def invoke(self, input=None, **kwargs):  # noqa: A002
                        if isinstance(input, dict):
                            return _run(**input)
                        if input is not None and not kwargs:
                            return _run(str(input))
                        return _run(**kwargs)

                    def __call__(self, *args, **kwargs):
                        if args:
                            return _run(str(args[0]))
                        return _run(**kwargs)

                return _GroundedWebSearchTool()

        schema = getattr(original, "args_schema", None)
        try:
            return StructuredTool.from_function(
                func=_run,
                name="web_search",
                description=description,
                args_schema=schema,
            )
        except Exception as exc:
            logger.warning(f"Failed to wrap web_search with StructuredTool: {exc}")
            return original

    def _select_research_lane_llm(self, user_text: str, callbacks: Optional[list] = None):
        """Return the one model selected for this Session and Turn."""
        self._last_model_route = "session"
        return getattr(self, "model_runtime", None)

    def _grounded_web_search(
        self,
        query: str,
        *,
        original_request: str = "",
        callbacks: Optional[list] = None,
        emit_tool_events: bool = True,
    ) -> str:
        """Single acquisition boundary for canonical grounded web search."""
        from agent.research import (
            resolve_web_search_queries,
            looks_like_multi_intent,
            _infer_city_from_text,
            _is_weather_clause,
            _normalize_weather_query,
            enrich_sports_query_with_subject,
        )

        raw_q = str(query or "").strip()
        if not raw_q:
            return ""
        research_binding = dict(getattr(self, "_active_research_binding", None) or {})
        active_task = getattr(self, "_active_task_run", None)
        active_requirement = next(
            (
                item for item in list(getattr(active_task, "requirements", None) or [])
                if item.requirement_id == str(research_binding.get("requirement_id") or "")
            ),
            None,
        )
        requirement_objective = str(
            getattr(active_requirement, "objective", "") or ""
        ).strip()
        requirement_id = str(research_binding.get("requirement_id") or "")
        attempt_id = str(research_binding.get("attempt_id") or "")
        canonical_task_search = bool(
            getattr(self, "_canonical_semantic_flow", False)
            and active_task is not None
        )
        if canonical_task_search and (not requirement_id or not attempt_id):
            logger.error(
                "Canonical web_search rejected missing TaskRun binding: task_run_id={} "
                "requirement_id={} attempt_id={}",
                str(getattr(active_task, "id", "") or ""),
                requirement_id,
                attempt_id,
            )
            return (
                "[WEB_SEARCH]\nexecution_status=error\n"
                "result_state=missing_runtime_binding\nretryable=false\n"
                "The canonical search attempt was missing its requirement identity."
            )
        try:
            from agent.retrieval_contracts import plan_research_query, query_plan_covers_requirement

            query_plan = plan_research_query(
                raw_q,
                resolved_entities=list(getattr(active_requirement, "entities", None) or []),
                requirement_id=requirement_id,
                attempt_id=attempt_id,
                objective=str(getattr(active_requirement, "objective", "") or ""),
                raw_user_message=str(
                    getattr(self, "_raw_turn_user_message", "")
                    or getattr(self, "_active_user_query", "")
                    or original_request
                    or ""
                ),
            )
            if active_requirement is not None and not query_plan_covers_requirement(
                query_plan, active_requirement
            ):
                raise ValueError("provider query does not retain the active requirement anchors")
            raw_q = query_plan.provider_query()
            self._last_research_query_plan = query_plan.model_dump(mode="json")
        except Exception as query_plan_exc:
            logger.warning("Research query plan rejected provider input: {}", query_plan_exc)
            if canonical_task_search:
                return (
                    "[WEB_SEARCH]\nexecution_status=error\n"
                    "result_state=query_plan_rejected\nretryable=true\n"
                    "The provider query did not preserve the active requirement anchors."
                )
            return (
                "[GROUNDED_SEARCH]\naccepted=false\n"
                "reason=query_plan_rejected\nDo not invent facts."
            )
        turn_constraints = set(
            getattr(getattr(self, "_execution_context", None), "constraints", []) or []
        )
        primary_sources_only = "primary_sources" in turn_constraints

        # Hard gate: local Desktop/project inspect must never become internet search
        # (model may still emit web_search; Stage 4 recovery also used to force it).
        hay = f"{requirement_objective or original_request or ''} {raw_q}".strip()
        if self._is_local_filesystem_intent(hay) or self._is_local_filesystem_intent(original_request or ""):
            if not re.search(
                r"(?i)\b(search the web|google|look up online|weather|forecast|"
                r"stock price|bitcoin price|news headlines)\b",
                hay,
            ):
                logger.info(
                    "Blocked web_search for local filesystem intent: {!r}",
                    (hay or "")[:100],
                )
                listing = str(getattr(self, "_last_local_project_listing", "") or "")
                samples = str(getattr(self, "_last_local_project_samples", "") or "")
                pin = str(getattr(self, "_last_local_project_path", "") or "")
                return (
                    "[LOCAL_FILESYSTEM — web_search blocked]\n"
                    "This turn is local Desktop/project work. Use file_list / file_read, not the internet.\n"
                    f"Pinned project: {pin or '(none yet)'}\n"
                    f"Listing:\n{listing[:3000]}\n"
                    f"Samples:\n{samples[:4000]}"
                ).strip()

        # Canonical research owns decomposition, capability selection, attempts,
        # retries, and evidence sufficiency at the TaskRun/ToolRun boundary. A
        # canonical web_search ToolRun therefore performs exactly one validated
        # acquisition. It must not hide SearchGrounder candidate loops, sports
        # shortcuts, page fetching, or model synthesis inside one successful tool
        # result. Legacy/noncanonical callers retain the compatibility grounder
        # below until their production consumers are retired.
        if canonical_task_search:
            output, provider_name = self._canonical_web_search_execute(
                raw_q,
                strategy=str(research_binding.get("strategy") or ""),
            )
            self._last_grounded_search_result = {
                "schema_version": 1,
                "chosen_query": raw_q,
                "accepted": False,
                "acquisition_only": True,
                "task_run_id": str(getattr(active_task, "id", "") or ""),
                "requirement_id": requirement_id,
                "attempt_id": attempt_id,
                "provider": provider_name,
                "query_plan": dict(self._last_research_query_plan or {}),
            }
            return str(output)

        # Classify every research request through the provider-neutral live
        # contract. A missing structured provider is explicit and falls back
        # to targeted browsing; it never fabricates a structured result.
        try:
            from agent.live_retrieval import LiveRetrievalRequest, LiveRetrievalRouter

            live_router = LiveRetrievalRouter()
            live_request = LiveRetrievalRequest(
                query=hay or raw_q,
                project_id=str(getattr(self._execution_context, "active_project_id", "") or ""),
                session_id=self._thread_key(),
            )
            live_route = live_router.route(live_request)
            self._last_live_retrieval_route = live_route.model_dump(mode="json")
            if live_route.domain.value == "flights_airports" and not live_route.adapter_name:
                self._last_structured_live_result = live_router.lookup(live_request).model_dump(mode="json")
        except Exception as live_route_exc:
            logger.debug("Live retrieval classification unavailable: {}", live_route_exc)
            self._last_live_retrieval_route = {}

        # --- Anti-loop: same turn must not re-run near-identical searches ---
        # Live: 3× identical "FIFA match list… today" rows from Stage3 + reflector + keep-trying.
        if not hasattr(self, "_request_grounded_results") or self._request_grounded_results is None:
            self._request_grounded_results = {}
        if not hasattr(self, "_request_grounded_count"):
            self._request_grounded_count = 0
        if not hasattr(self, "_request_grounded_inflight") or self._request_grounded_inflight is None:
            self._request_grounded_inflight = set()
        task_run_id = str(getattr(active_task, "id", "") or "")
        freshness_class = str(
            getattr(active_requirement, "freshness_class", "") or "unspecified"
        )

        def _cache_key(value: str) -> str:
            return self._scoped_search_fingerprint(
                value,
                task_run_id=task_run_id,
                requirement_id=requirement_id,
                freshness_class=freshness_class,
            )

        # The full composite user request is deliberately excluded. Unrelated
        # child requirements must never share a cache or in-flight identity.
        fps = [_cache_key(raw_q)]
        fps = [f for f in fps if f]

        def _settle_search_cache(
            packet: str, extra_queries: Optional[Iterable[str]] = None
        ) -> None:
            store_keys = [*fps, _cache_key(raw_q)]
            store_keys.extend(
                _cache_key(value) for value in list(extra_queries or [])
            )
            for key in {item for item in store_keys if item}:
                if str(packet or "").strip():
                    self._request_grounded_results[key] = str(packet)
                self._request_grounded_inflight.discard(key)

        for f in fps:
            if f in self._request_grounded_results:
                cached = self._request_grounded_results[f]
                if cached is not None and str(cached).strip():
                    logger.info("Search loop suppressed (fingerprint hit): {!r}", raw_q[:90])
                    return str(cached)
            if f in self._request_grounded_inflight:
                logger.info("Search loop suppressed (in-flight): {!r}", raw_q[:90])
                return (
                    "[GROUNDED_SEARCH]\n"
                    "accepted=false\n"
                    "reason=search_in_flight\n"
                    "Do not invent facts.\n"
                )
        # Hard cap: one sports or two general acquisitions per requirement.
        sports_cap = bool(
            re.search(r"(?i)\b(fifa|world cup|kickoff|schedule|fixtures?|mnt|timezone)\b", raw_q)
        )
        cap = 1 if sports_cap else 2
        count_scope = f"{task_run_id}:{requirement_id or '_unbound'}"
        if (
            not hasattr(self, "_request_grounded_count_by_requirement")
            or self._request_grounded_count_by_requirement is None
        ):
            self._request_grounded_count_by_requirement = {}
        scoped_count = int(
            self._request_grounded_count_by_requirement.get(count_scope, 0) or 0
        )
        if scoped_count >= cap:
            logger.info(
                "Search loop hard-cap ({}) for requirement {} with no exact cache hit",
                cap,
                requirement_id or "_unbound",
            )
            return (
                "[GROUNDED_SEARCH]\n"
                "accepted=false\n"
                "reason=search_budget_exhausted\n"
                "Do not invent facts. Use any prior search evidence already in context.\n"
            )
        self._request_grounded_count = int(self._request_grounded_count or 0) + 1
        self._request_grounded_count_by_requirement[count_scope] = scoped_count + 1
        for f in fps:
            self._request_grounded_inflight.add(f)
            # Placeholder so concurrent re-entry sees in-flight
            self._request_grounded_results.setdefault(f, "__inflight__")

        # --- Live sports structured path (default for scores/odds; not crawl search) ---
        # Category mismatch: Tavily/etc. return crawled pages; live scores need APIs.
        try:
            from agent.sports_data import (
                get_sports_data_client,
                is_live_sports_data_intent,
            )
            from config import config as _cfg

            sports_src = str(
                requirement_objective
                or original_request
                or raw_q
                or ""
            ).strip()
            prefer_live = bool(getattr(_cfg, "sports_live_enabled", True)) and (
                is_live_sports_data_intent(sports_src) or is_live_sports_data_intent(raw_q)
            )
            # Multi-intent: only use sports_live for sports-shaped sub-queries, not whole weather+score
            domains_live = False
            try:
                from agent.research import intent_domains as _idom

                d = _idom(sports_src)
                domains_live = ("odds" in d) or (
                    "sports" in d and is_live_sports_data_intent(sports_src)
                )
            except Exception:
                domains_live = prefer_live
            if prefer_live and domains_live:
                # If multi-intent includes weather etc., only short-circuit pure live sports turns
                multi_other = False
                try:
                    from agent.research import intent_domains as _idom2, looks_like_multi_intent

                    doms = _idom2(sports_src)
                    multi_other = looks_like_multi_intent(sports_src) and (
                        "weather" in doms or "finance" in doms or "entertainment" in doms or "news" in doms
                    )
                except Exception:
                    multi_other = False
                if not multi_other:
                    client = get_sports_data_client()
                    live = client.query(sports_src or raw_q)
                    if live.ok:
                        try:
                            from agent.live_retrieval import structured_sports_result

                            self._last_structured_live_result = structured_sports_result(
                                live,
                                query=sports_src or raw_q,
                            ).model_dump(mode="json")
                        except Exception as structured_exc:
                            logger.debug("Sports common-result projection failed: {}", structured_exc)
                        packet = live.as_tool_text()
                        # When LC already owns a web_search ToolRun, do NOT emit a second
                        # sports_live row — return packet so the outer tool_end is the
                        # single user-facing completion for this logical search.
                        outer_sports = self._get_outer_web_search_id()
                        if emit_tool_events and not outer_sports:
                            ground_run_id = str(uuid.uuid4())
                            self._emit_tool_start(
                                callbacks,
                                "sports_live",
                                sports_src or raw_q,
                                ground_run_id,
                            )
                            self._emit_tool_end(callbacks, packet, ground_run_id)
                        try:
                            self._last_grounded_search_result = {
                                "chosen_query": sports_src or raw_q,
                                "accepted": True,
                                "provider": "sports_live",
                                "mode": live.mode,
                                "condensed_evidence": live.summary,
                            }
                        except Exception:
                            pass
                        logger.info(
                            "Sports live path ok mode={} sport={}",
                            live.mode,
                            live.sport_key,
                        )
                        _settle_search_cache(packet)
                        return packet
                    else:
                        logger.info(
                            "Sports live path miss ({}), falling back to web_search",
                            live.error[:120] if live.error else "unknown",
                        )
        except Exception as _sports_exc:
            logger.debug("Sports live path skipped: {}", _sports_exc)

        # Prefer the richest multi-intent source: active user turn > original_request > tool arg.
        # Stage 3 used to pass _extract_search_query() (single primary) as original_request,
        # which wiped FIFA+weather down to one query — never do that again.
        active = requirement_objective
        orig_in = str(original_request or "").strip()
        candidates_src = [active, raw_q] if active else [orig_in, raw_q]
        orig = raw_q
        try:
            from agent.research import intent_domains as _intent_domains

            best_score = -1
            for src in candidates_src:
                if not src:
                    continue
                score = len(_intent_domains(src)) * 10 + (1 if looks_like_multi_intent(src) else 0) + min(len(src), 200) / 200.0
                if score > best_score:
                    best_score = score
                    orig = src
        except Exception:
            orig = active or orig_in or raw_q
        # Recipe fast path + general multi-intent decomposition fallback.
        # Never silent-overwrite multi with the model's single tool arg.
        llm_invoke = None
        try:
            wrap = self._select_research_lane_llm(orig, callbacks=callbacks)
            if wrap is not None and hasattr(wrap, "invoke_fast"):
                llm_invoke = lambda p, _w=wrap: _w.invoke_fast(p, max_tokens=180)
            elif wrap is not None and hasattr(wrap, "invoke"):
                llm_invoke = lambda p, _w=wrap: _w.invoke(p)
        except Exception:
            llm_invoke = None
        multi = resolve_web_search_queries(
            orig,
            raw_q,
            llm_invoke=llm_invoke,
            use_decomposition=True,
        )
        if not multi:
            multi = resolve_web_search_queries(raw_q, raw_q, llm_invoke=None, use_decomposition=False)
        if not multi:
            multi = [raw_q]
        validated_multi: list[str] = []
        validated_plans: list[dict[str, Any]] = []
        for candidate_query in multi:
            try:
                candidate_plan = plan_research_query(
                    str(candidate_query or ""),
                    resolved_entities=list(getattr(active_requirement, "entities", None) or []),
                    requirement_id=str(research_binding.get("requirement_id") or ""),
                    attempt_id=str(research_binding.get("attempt_id") or ""),
                    objective=str(getattr(active_requirement, "objective", "") or ""),
                )
                if active_requirement is not None and not query_plan_covers_requirement(
                    candidate_plan, active_requirement
                ):
                    raise ValueError("decomposed query lost the active requirement anchors")
                validated_multi.append(candidate_plan.provider_query())
                validated_plans.append(candidate_plan.model_dump(mode="json"))
            except Exception as candidate_exc:
                logger.warning("Rejected contaminated research sub-query: {}", candidate_exc)
        multi = list(dict.fromkeys(validated_multi)) or [raw_q]
        self._last_research_query_plans = validated_plans or [dict(self._last_research_query_plan or {})]
        if len(multi) > 1:
            primary_query = str(multi[0] or "").strip()
            anchored = [primary_query]
            for followup_query in multi[1:]:
                followup = str(followup_query or "").strip()
                if (
                    primary_query
                    and re.search(r"(?i)^\s*(?:recommend|compare|choose|best value|which (?:one|option))\b", followup)
                    and not any(
                        term in followup.lower()
                        for term in re.findall(r"[a-z0-9]{4,}", primary_query.lower())
                        if term not in {"best", "under", "with", "from", "streaming"}
                    )
                ):
                    followup = f"{followup} for {primary_query}"
                anchored.append(followup)
            multi = anchored
        # Bare "check the weather" with no city: prefer last subject / web context / profile location.
        subject_city = self._resolve_weather_city_hint(orig)
        if subject_city:
            fixed: list[str] = []
            for q in multi:
                if _is_weather_clause(q) and not _infer_city_from_text(q):
                    fixed.append(_normalize_weather_query(q, city_hint=subject_city))
                else:
                    fixed.append(q)
            multi = fixed
        # Schedule follow-ups inherit league only from a sports prior subject.
        # Never inject an unrelated prior subject (e.g. Pokémon) into a FIFA ask.
        # Sports subject: ONLY enrich when the CURRENT user request is sports-shaped
        # and the prior subject is the SAME competition/team family — never append
        # stale fixture names (e.g. "Argentina vs Egypt") onto a fresh England ask.
        subject_ctx = str(
            getattr(self, "_current_subject_text", "")
            or getattr(self, "_last_web_query_context", "")
            or ""
        ).strip()
        current_ask = str(orig or raw_q or "").strip()
        subject_is_sports = bool(
            subject_ctx
            and (
                re.search(
                    r"(?i)\b(fifa|world\s*cup|nhl|nba|nfl|mlb|match|score|fixture|kickoff|vs\.?|versus)\b",
                    subject_ctx,
                )
                or self._topic_template_from_subject(subject_ctx) == "sports"
            )
        )
        ask_is_sports = bool(
            re.search(
                r"(?i)\b(fifa|world\s*cup|nhl|nba|nfl|mlb|fixture|match|game|kickoff|score|"
                r"odds|outright|winner|plays? next|schedule)\b",
                current_ask,
            )
        )
        # Detect stale fixture injection: prior subject has "A vs B" but current ask
        # names a different team/topic without those sides.
        prior_sides = re.findall(
            r"(?i)\b([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)\s+vs\.?\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)\b",
            subject_ctx,
        )
        ask_has_named_team = bool(
            re.search(
                r"(?i)\b(england|france|brazil|germany|spain|argentina|egypt|morocco|"
                r"canada|mexico|usa|united states|portugal|netherlands|italy|japan)\b",
                current_ask,
            )
        )
        skip_sports_enrich = False
        if prior_sides and ask_has_named_team:
            for a, b in prior_sides:
                if a.lower() not in current_ask.lower() and b.lower() not in current_ask.lower():
                    # Prior fixture is unrelated to this ask — do not merge.
                    skip_sports_enrich = True
                    break
        # New-objective sports questions: prefer the raw user ask over stale subject.
        if (
            subject_ctx
            and subject_is_sports
            and ask_is_sports
            and not skip_sports_enrich
            and not re.search(r"(?i)\bvs\.?\b", current_ask)
        ):
            enriched: list[str] = []
            for q in multi:
                sports_shaped = bool(
                    self._is_schedule_time_query(str(q).lower())
                    or re.search(
                        r"(?i)\b(fifa|world cup|nhl|nba|nfl|mlb|fixture|match|game|kickoff|score)\b",
                        str(q),
                    )
                )
                # Already names competition or a specific team — leave alone.
                if sports_shaped and not re.search(
                    r"(?i)\b(fifa|world\s*cup|nhl|nba|nfl|mlb|england|brazil|france|"
                    r"germany|spain|argentina|odds|outright|winner)\b",
                    str(q),
                ):
                    enriched.append(enrich_sports_query_with_subject(q, subject_ctx))
                else:
                    enriched.append(q)
            multi = enriched
        # Tournament outright-winner odds: strip accidental matchup language.
        if re.search(r"(?i)\b(who will win|outright|tournament winner|win the world cup)\b", current_ask):
            multi = [
                re.sub(
                    r"(?i)\b[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?\s+vs\.?\s+[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?\b",
                    " ",
                    q,
                ).strip()
                if re.search(r"(?i)\b(odds|winner|win)\b", q)
                else q
                for q in multi
            ]
            multi = [
                (
                    f"{q} FIFA World Cup outright winner betting odds"
                    if re.search(r"(?i)\bodds|winner|win\b", q)
                    and "outright" not in q.lower()
                    else q
                )
                for q in multi
            ]
        try:
            logger.info(
                "Search multi-intent resolved n={} queries={} orig={!r}",
                len(multi),
                multi,
                (orig or "")[:120],
            )
        except Exception:
            pass

        if not bool(getattr(config, "search_grounding_enabled", True)) and not primary_sources_only:
            chunks = []
            outer_id = self._get_outer_web_search_id()
            canonical_run_id = ""
            if emit_tool_events and not outer_id:
                canonical_run_id = str(uuid.uuid4())
                self._emit_tool_start(callbacks, "web_search", raw_q, canonical_run_id)
            for q in multi:
                try:
                    raw = self._raw_web_search_execute(q)
                    if raw:
                        chunks.append(f"### Search: {q}\n{raw}")
                except Exception as exc:
                    _settle_search_cache("", multi)
                    if canonical_run_id:
                        self._emit_tool_error(callbacks, exc, canonical_run_id)
                    raise
            joined_raw = "\n\n".join(chunks)
            if canonical_run_id:
                self._emit_tool_end(callbacks, joined_raw, canonical_run_id)
            _settle_search_cache(joined_raw, multi)
            return joined_raw

        # Default 2 candidates — enough retry without 3× waste per intent.
        # Already-rich schedule/TZ queries get a single candidate (no variant storm).
        max_cands = int(getattr(config, "search_grounding_max_candidates", 2) or 2)
        rich_schedule = any(
            re.search(
                r"(?i)\b(kickoff|convert|timezone|mnt|mountain|fixtures?|match list)\b",
                q,
            )
            and re.search(r"(?i)\b(today|tomorrow|thursday|friday|\d{4})\b", q)
            for q in multi
        )
        if rich_schedule:
            max_cands = 1
        grounder = SearchGrounder(
            max_candidates=max(1, min(max_cands, 3)),
            primary_sources_only=primary_sources_only,
        )
        current_subject = str(
            getattr(self, "_current_subject_text", "")
            or getattr(self, "_last_web_query_context", "")
            or ""
        )

        def execute_candidate(candidate_query: str) -> str:
            try:
                return self._raw_web_search_execute(candidate_query)
            except Exception as exc:
                logger.warning("Search candidate failed for {!r}: {}", candidate_query[:80], exc)
                telemetry = getattr(self, "_verification_telemetry", None)
                if telemetry is not None:
                    telemetry.record(
                        "search_evidence_irrelevant",
                        tool="web_search",
                        reason=f"Search tool failed: {exc}",
                        metadata={"query": candidate_query},
                    )
                return ""

        # Deduplicate multi list by fingerprint (near-identical FIFA/TZ variants)
        deduped_multi: list[str] = []
        seen_fp: set[str] = set()
        for q in multi:
            qfp = self._search_query_fingerprint(q)
            if qfp in seen_fp:
                continue
            seen_fp.add(qfp)
            deduped_multi.append(q)
        multi = deduped_multi or multi
        planned_multi: list[str] = []
        for candidate in multi:
            try:
                from agent.retrieval_contracts import plan_research_query

                planned_multi.append(plan_research_query(str(candidate or "")).provider_query())
            except Exception as candidate_plan_exc:
                logger.warning("Rejected contaminated search candidate {!r}: {}", candidate, candidate_plan_exc)
        multi = planned_multi or [raw_q]
        # Single-domain schedule/sports asks are one logical research intent —
        # never fan out near-duplicate FIFA/fixture variants into multiple UI rows.
        try:
            from agent.research import intent_domains as _idom_single, looks_like_multi_intent as _lmi

            if not _lmi(orig) and len(multi) > 1:
                doms = _idom_single(orig)
                if len(doms) <= 1:
                    multi = [multi[0]]
        except Exception:
            if len(multi) > 1 and not re.search(
                r"(?i)\b(and also|also|plus|as well as|and then)\b", orig or ""
            ):
                # Conservative collapse when multi-intent detection is unavailable
                multi = [multi[0]]

        formatted_parts: list[str] = []
        # Source identity is canonical evidence metadata, not synthesis context.
        # Keep it in a separate append-only ledger so progressive compaction can
        # replace prose without erasing the sources that produced it.
        source_ledger: list[dict[str, str]] = []
        source_ledger_keys: set[tuple[str, str]] = set()
        last_grounded = None
        any_accepted = False
        # ── ToolRun identity for web_search ──────────────────────────────
        # Provider candidates, rewrites, and multi-intent branches are attempt
        # diagnostics under one canonical web_search ToolRun. They never create
        # top-level child/wrapper ToolRuns.
        outer_id = self._get_outer_web_search_id()
        self._grounded_fanout_count = 0
        canonical_run_id = ""
        if emit_tool_events and not outer_id:
            canonical_run_id = str(uuid.uuid4())
            self._emit_tool_start(callbacks, "web_search", raw_q, canonical_run_id)
        for q in multi:
            try:
                grounded = grounder.ground(
                    original_request=orig,
                    resolved_request=q,
                    current_subject=current_subject,
                    execute=execute_candidate,
                    fetch_url=self._fetch_search_result_page_text,
                )
            except Exception as exc:
                if canonical_run_id:
                    self._emit_tool_error(callbacks, exc, canonical_run_id)
                raise
            last_grounded = grounded
            any_accepted = any_accepted or bool(grounded.accepted)
            for item in list(grounded.evidence or []):
                url = str(getattr(item, "url", "") or "").strip()
                title = re.sub(r"\s+", " ", str(getattr(item, "title", "") or "")).strip()
                if not url:
                    continue
                key = (url.casefold(), str(grounded.chosen_query or q).casefold())
                if key in source_ledger_keys:
                    continue
                source_ledger_keys.add(key)
                source_ledger.append({
                    "query": str(grounded.chosen_query or q).strip()[:500],
                    "title": title[:500],
                    "url": url[:2000],
                    "accepted": "true" if grounded.accepted else "false",
                })
                if len(source_ledger) >= 24:
                    break
            part = format_grounded_tool_output(grounded)
            if len(multi) > 1:
                part = f"### Search: {q}\n{part}"
            formatted_parts.append(part)

            # Tongyi-inspired progressive synthesis (after 3 searches)
            if len(formatted_parts) >= 3 and q != multi[-1]:
                try:
                    logger.info("[Research Synthesis] Running progressive synthesis to prevent context bloat.")
                    accumulated = "\n\n".join(formatted_parts)
                    synthesis_prompt = (
                        f"Original Request: {orig}\n\n"
                        f"Here is the accumulated raw search evidence so far:\n\n"
                        f"{accumulated}\n\n"
                        "Synthesize this evidence into a highly compact, dense list of key facts (dates, numbers, scores, news). "
                        "Keep only the direct answers to the query. Remove all search metadata, formatting instructions, and HTML/link chrome. "
                        "Length limit: 1200 characters."
                    )
                    wrap = self._select_research_lane_llm(orig, callbacks=callbacks)
                    compact_summary = wrap.invoke(synthesis_prompt)
                    # Replace formatted_parts with the compact summary
                    formatted_parts = [f"### Synthesized Evidence (first 3 queries):\n{compact_summary}"]
                    logger.info(f"[Research Synthesis] Condensed evidence size: {len(compact_summary)} chars")
                except Exception as synth_exc:
                    logger.warning("Progressive research synthesis failed: {}", synth_exc)
            try:
                status = "accepted" if grounded.accepted else "insufficient"
                # loguru uses {} formatting, not %-style
                logger.info(
                    "Search grounding {} query={!r} evidence={}",
                    status,
                    grounded.chosen_query,
                    len(grounded.evidence or []),
                )
            except Exception:
                pass
            telemetry = getattr(self, "_verification_telemetry", None)
            if telemetry is not None:
                for rejected in grounded.rejected_candidates:
                    telemetry.record(
                        "search_query_rejected",
                        tool="web_search",
                        reason=str(rejected.get("reason") or "Search candidate rejected."),
                        metadata={
                            "query": rejected.get("query"),
                            "score": rejected.get("score"),
                            "original_request": orig,
                        },
                    )
                if not grounded.accepted:
                    telemetry.record(
                        "search_evidence_insufficient",
                        tool="web_search",
                        reason="No grounded search candidate reached the relevance threshold.",
                        metadata={"chosen_query": grounded.chosen_query, "original_request": orig},
                    )

        if last_grounded is not None:
            try:
                self._last_grounded_search_result = last_grounded.as_dict()
                self._last_grounded_search_result["multi_queries"] = list(multi)
                self._last_grounded_search_result["any_accepted"] = any_accepted
                self._last_grounded_search_result["source_ledger"] = list(source_ledger)
            except Exception:
                self._last_grounded_search_result = {
                    "chosen_query": last_grounded.chosen_query,
                    "accepted": last_grounded.accepted,
                    "condensed_evidence": last_grounded.condensed_evidence,
                    "multi_queries": list(multi),
                    "source_ledger": list(source_ledger),
                }

        joined = "\n\n".join(formatted_parts)
        if source_ledger:
            ledger_lines = ["### Immutable Source Ledger"]
            for source in source_ledger:
                title = source["title"] or source["url"]
                ledger_lines.append(
                    f"- [{title}]({source['url']}) "
                    f"(query: {source['query']}; accepted={source['accepted']})"
                )
            joined = f"{joined}\n\n" + "\n".join(ledger_lines)
        if canonical_run_id:
            self._emit_tool_end(callbacks, joined, canonical_run_id)
        # Store for anti-loop re-entry (model/reflector/retry with near-identical query)
        try:
            _settle_search_cache(joined, multi)
        except Exception:
            pass
        # Anchor teams/times from evidence even if the model later answers vaguely
        try:
            evidence_blob = joined
            if last_grounded is not None:
                evidence_blob = f"{evidence_blob}\n{last_grounded.condensed_evidence or ''}\n{last_grounded.raw_output or ''}"
            facts = self._extract_answer_anchor_facts(evidence_blob)
            if facts:
                self._last_search_facts = facts
                cur = str(getattr(self, "_current_subject_text", "") or "").strip()
                low_cur = cur.lower()
                extra_bits = []
                for phrase in re.findall(
                    r"[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?\s+vs\.?\s+[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?|"
                    r"\d{1,2}\s*(?:a\.?m\.?|p\.?m\.?|am|pm)|\$[\d.]+",
                    facts,
                    flags=re.IGNORECASE,
                ):
                    if phrase.lower() not in low_cur:
                        extra_bits.append(phrase)
                if extra_bits:
                    merged = f"{cur} {' '.join(extra_bits)}".strip() if cur else " ".join(extra_bits)
                    self._current_subject_text = merged[:280]
        except Exception:
            pass

        return joined

    def _fetch_search_result_page_text(self, url: str, *, timeout: float = 6.0, max_chars: int = 12000) -> str:
        """Read-only bounded page text extraction for search grounding fallbacks.

        For weather pages, prefer windows of text that contain temperature numbers
        so nav chrome / cookie banners don't drown out the forecast.
        """
        raw_url = str(url or "").strip()
        if not re.match(r"^https?://", raw_url, flags=re.IGNORECASE):
            return ""
        try:
            from html import unescape
            from urllib.request import Request, urlopen

            req = Request(
                raw_url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (compatible; EchoSpeakSearchGrounder/1.1; "
                        "+https://github.com/echospeak)"
                    )
                },
            )
            with urlopen(req, timeout=timeout) as resp:
                content_type = str(resp.headers.get("content-type") or "").lower()
                if content_type and "text/html" not in content_type and "text/plain" not in content_type:
                    return ""
                raw = resp.read(max_chars * 3)
            text = raw.decode("utf-8", errors="ignore")
            text = re.sub(r"(?is)<(script|style|noscript|svg|canvas).*?</\1>", " ", text)
            text = re.sub(r"(?s)<[^>]+>", " ", text)
            text = unescape(text)
            text = re.sub(r"\s+", " ", text).strip()
            if not text:
                return ""
            # Keep temperature-dense windows when present (weather deep-fetch).
            windows: list[str] = []
            for m in re.finditer(
                r".{0,120}(?:\d+\s*°\s*[CFcf]|high\s+\d+|low\s+\d+|feels like\s+-?\d+).{0,160}",
                text,
                flags=re.IGNORECASE,
            ):
                chunk = m.group(0).strip()
                if chunk and chunk not in windows:
                    windows.append(chunk)
                if len(windows) >= 8:
                    break
            if windows:
                focused = " … ".join(windows)
                return focused[:max_chars]
            return text[:max_chars]
        except Exception:
            return ""

    def _extract_dates_from_text(self, text: str, default_year: int) -> list[datetime]:
        t = (text or "")
        if not t.strip():
            return []

        out: list[datetime] = []

        for m in re.finditer(r"\b(20\d{2})-(\d{2})-(\d{2})\b", t):
            try:
                out.append(datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))))
            except Exception:
                continue

        for m in re.finditer(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b", t):
            try:
                out.append(datetime(int(m.group(3)), int(m.group(1)), int(m.group(2))))
            except Exception:
                continue

        month_map = {
            "jan": 1,
            "january": 1,
            "feb": 2,
            "february": 2,
            "mar": 3,
            "march": 3,
            "apr": 4,
            "april": 4,
            "may": 5,
            "jun": 6,
            "june": 6,
            "jul": 7,
            "july": 7,
            "aug": 8,
            "august": 8,
            "sep": 9,
            "sept": 9,
            "september": 9,
            "oct": 10,
            "october": 10,
            "nov": 11,
            "november": 11,
            "dec": 12,
            "december": 12,
        }

        month_re = r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
        for m in re.finditer(rf"\b({month_re})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,\s*)?(20\d{{2}})?\b", t, flags=re.IGNORECASE):
            mon = month_map.get(m.group(1).lower())
            if not mon:
                continue
            try:
                day = int(m.group(2))
            except Exception:
                continue
            year_s = (m.group(3) or "").strip()
            try:
                year = int(year_s) if year_s else int(default_year)
            except Exception:
                year = int(default_year)
            try:
                out.append(datetime(year, int(mon), int(day)))
            except Exception:
                continue

        return out

    def _is_direct_time_question(self, query_lower: str) -> bool:
        q = (query_lower or "").strip()
        if not q:
            return False

        direct_time_phrases = [
            "what time is it",
            "time is it",
            "current time",
            "what day is it",
            "what day is today",
            "whats the day today",
            "what's the day today",
            "what day today",
            "what is the day today",
            "what day",
            "what day today",
            "today is what day",
            "what date is it",
            "what date",
            "current date",
            "today's date",
            "todays date",
            "date today",
        ]
        if not any(p in q for p in direct_time_phrases):
            return False

        schedule_markers = [
            "what time does",
            "start time",
            "starts at",
            "kickoff",
            "tipoff",
            "game",
            "match",
            "fixture",
            "schedule",
            "event",
            "concert",
            "show",
            "flight",
            "departure",
            "arrival",
            "release",
            "launch",
        ]
        if any(m in q for m in schedule_markers):
            return False

        return True

    def _is_schedule_time_query(self, query_lower: str) -> bool:
        q = (query_lower or "").strip()
        if not q:
            return False

        time_ask = [
            "what time does",
            "when does",
            "when is",
            "when's",
            "start time",
            "starts at",
            "kickoff",
            "tipoff",
        ]
        if any(t in q for t in time_ask) and self._has_schedule_terms(q):
            return True
        # Near-future fixture slate: "who's playing tomorrow", "what games today"
        if re.search(r"\b(today|tonight|tomorrow|this weekend)\b", q) and (
            re.search(r"\bwho(?:'s| is)?\s+playing\b", q)
            or re.search(r"\bwhat\s+(?:games?|matches?)\b", q)
            or (self._has_schedule_terms(q) and re.search(r"\b(world cup|fifa|nhl|nba|nfl|mlb)\b", q))
        ):
            return True
        return False

    def _is_hardware_capability_query(self, query_lower: str) -> bool:
        q = (query_lower or "").strip()
        if not q:
            return False

        hardware_terms = [
            "my pc",
            "my computer",
            "my laptop",
            "my rig",
            "hardware",
            "specs",
            "cpu",
            "gpu",
            "vram",
            "ram",
            "memory",
        ]
        model_terms = [
            "model",
            "llm",
            "gguf",
            "quant",
            "q4",
            "q5",
            "q8",
            "kimi",
            "k2.5",
            "gpt-oss",
            "ollama",
            "lm studio",
        ]
        intent_terms = [
            "can i run",
            "can my",
            "will it run",
            "run it",
            "handle",
            "support",
            "fit",
            "load",
            "try",
            "testing",
            "use",
            "works on",
            "work with",
        ]

        has_hardware = any(t in q for t in hardware_terms)
        has_model = any(t in q for t in model_terms)
        has_intent = any(t in q for t in intent_terms)
        return (has_intent and (has_hardware or has_model)) or (has_hardware and has_model)

    def _extract_user_request_text(self, text: str) -> str:
        """Extract the actual user request from Discord bot wrapped inputs.

        Discord bot sometimes sends:
          "Recent conversation context:\n...\n\nUser request: <message>"
        But older/buggy paths may omit the marker and include lines like:
          "Recent conversation context:\nUser: <message>\nEchoSpeak: ..."
        For routing/tool selection, we only want the user's latest request, not the injected context.
        """
        try:
            raw = (text or "").strip()
            if not raw:
                return raw

            low = raw.lower()
            marker = "user request:"
            idx = low.rfind(marker)
            if idx != -1:
                return (raw[idx + len(marker) :] or "").strip()

            # Fallback: if this is a context block, use the last "User:" line.
            if "recent conversation context:" in low and "user:" in low:
                matches = re.findall(r"(?im)^\s*user\s*:\s*(.+?)\s*$", raw)
                if matches:
                    return (matches[-1] or "").strip()

            # If this is a context-only payload with no user line, don't route tools off it.
            if "recent conversation context:" in low and "user request:" not in low and "user:" not in low:
                return ""

            return raw
        except Exception:
            return (text or "").strip()

    def _allow_llm_tool_calling(self) -> bool:
        """Equal access: every configured provider may attempt native tool calling.

        No provider- or model-name allowlists. Native-call parsing remains in
        the selected model-family adapter; malformed responses stay inside the
        bounded canonical repair loop.
        Explicit opt-out only via DISABLE_NATIVE_TOOL_CALLING=true.
        USE_TOOL_CALLING_LLM remains an Ollama format-wrapper opt-in and does
        not gate whether tool-capable stages may be attempted.
        """
        if bool(getattr(config, "disable_native_tool_calling", False)):
            return False
        return True

    def _tool_calling_diagnostics(self) -> Dict[str, Any]:
        selected_model_id = self._selected_model_id()
        family_adapter = get_family_adapter(selected_model_id, self.llm_provider.value)
        native_supported = bool(family_adapter.capabilities.native_tool_calls)
        native_enabled = bool(self._allow_llm_tool_calling() and native_supported)
        return {
            "provider": self.llm_provider.value,
            "model": selected_model_id,
            "model_family": family_adapter.family.value,
            "chat_template": family_adapter.template,
            "adapter_version": family_adapter.version,
            "model_turn_contract_version": "8.0.0",
            "semantic_runtime": "lean",
            "execution_loop": "lean",
            "native_tool_calling_supported": native_supported,
            "native_tool_calling_enabled": native_enabled,
            "action_parser_enabled": bool(getattr(config, "action_parser_enabled", True)),
            "printed_tool_syntax_executable": False,
            "lmstudio_tool_calling": bool(getattr(config, "lmstudio_tool_calling", False)),
            "use_tool_calling_llm": bool(getattr(config, "use_tool_calling_llm", False)),
            "disable_native_tool_calling": bool(getattr(config, "disable_native_tool_calling", False)),
            "last_tool_calling_mode": str(getattr(self, "_last_tool_calling_mode", "") or ""),
            "last_stage4_branch": str(getattr(self, "_last_stage4_branch", "") or ""),
            "current_subject": str(getattr(self, "_current_subject_text", "") or ""),
        }

    def _selected_model_id(self) -> str:
        """Return the bound model id without requiring test/provider shims to own it."""
        return str(
            getattr(getattr(self, "model_runtime", None), "model_id", "")
            or dict(getattr(self, "provider_info", {}) or {}).get("model")
            or getattr(getattr(config, "local", None), "model_name", "")
            or "default"
        )

    def _apply_bound_requirement_evidence(self, evidence: Any, artifact_id: str = "") -> None:
        """Apply normalized evidence to the sole TaskRun requirement ledger."""
        task = getattr(self, "_active_task_run", None)
        if task is None or evidence is None:
            return
        from agent.research_runtime import apply_evidence_to_state
        from agent.task_runs import get_task_run_store

        store = get_task_run_store()
        current = store.get(task.id, session_id=task.session_id, project_id=task.project_id)
        if current is None:
            return
        requirement = next(
            (item for item in current.requirements if item.requirement_id == evidence.requirement_id),
            None,
        )
        state = current.requirement_states.get(str(evidence.requirement_id or ""))
        if requirement is None or state is None:
            raise RuntimeError("Tool evidence does not belong to a current TaskRun requirement")
        states = dict(current.requirement_states)
        states[requirement.requirement_id] = apply_evidence_to_state(
            requirement,
            state,
            evidence,
            artifact_id=artifact_id,
            budget=current.research_budget,
        )
        current = store.update(
            current.id,
            session_id=current.session_id,
            project_id=current.project_id,
            expected_revision=current.revision,
            requirement_states=states,
            clear_fields=("liveness_decision",),
            research_artifact_ids=list(dict.fromkeys([
                *current.research_artifact_ids,
                *([artifact_id] if artifact_id else []),
            ])),
            tool_run_ids=list(dict.fromkeys([*current.tool_run_ids, evidence.tool_run_id])),
            workflow_stage="evidence_evaluated",
            last_execution_id=str(getattr(self, "_current_execution_id", "") or ""),
        )
        self._active_task_run = current
        self._emit_active_task_activity(current)

    @staticmethod
    def _sanitize_tool_preview(tool_name: str, output: str) -> str:
        """Return a bounded diagnostic summary without leaking file bodies."""

        raw = str(output or "").strip()
        try:
            from agent.tools import strip_echo_file_wrapper

            cleaned = strip_echo_file_wrapper(raw)
        except Exception:
            cleaned = raw
        cleaned = re.sub(
            r"<<<ECHO_FILE\b[^>]*>>>|<<<END_ECHO_FILE>>>", "", cleaned, flags=re.I
        )
        cleaned = re.sub(
            r"(?im)^(Read|Wrote|Appended)\s+\d+\s+chars\b.*$", "", cleaned
        ).strip()
        first = re.sub(r"\s+", " ", cleaned.splitlines()[0] if cleaned else "")
        return first[:240] or (f"{tool_name} completed" if tool_name else "completed")

    def _tool_calling_mode_label(self) -> str:
        diag = self._tool_calling_diagnostics()
        if diag.get("native_tool_calling_enabled"):
            return "canonical_native_tool_calls"
        if diag.get("action_parser_enabled"):
            return "canonical_structured_decision"
        return "canonical_text_only"

    def get_last_doc_sources(self) -> list:
        return list(self._last_doc_sources or [])

    def _should_auto_confirm(self, tool_name: str = "") -> bool:
        """Check if current source/role should auto-execute action tools without confirmation.

        Role-based auto-confirm policy:
          - OWNER:   auto-confirm safe + moderate tools; destructive still requires confirm.
          - TRUSTED: auto-confirm safe tools only; moderate + destructive require confirm.
          - PUBLIC:  NEVER auto-confirm anything (public users shouldn't reach action tools
                     at all due to role blocking, but this is a safety net).
        """
        src = str(getattr(self, "_current_source", None) or "").strip().lower()
        constraints = set(getattr(getattr(self, "_execution_context", None), "constraints", []) or [])
        if "wait_for_approval" in constraints:
            return False
        
        # Web UI / localhost: NEVER auto-confirm mutating tools.
        if not src or src == "web":
            return False
        # Mutating tools never auto-confirm except explicit Discord DM owner policy below.
        if tool_name in {
            "file_write", "file_delete", "file_move", "file_copy", "file_mkdir",
            "artifact_write", "terminal_run",
        } and src not in {"discord_bot_dm"}:
            return False

        if src != "discord_bot_dm":
            return False
        if not bool(getattr(config, "discord_bot_auto_confirm", False)):
            return False

        from config import DiscordUserRole
        from agent.tools import TOOL_METADATA

        role = getattr(self, "_current_user_role", DiscordUserRole.PUBLIC)

        # Public users — never auto-confirm
        if role == DiscordUserRole.PUBLIC:
            logger.info(f"Auto-confirm blocked for PUBLIC user, tool='{tool_name}' (source={src})")
            return False

        meta = TOOL_METADATA.get(tool_name, {})
        risk = meta.get("risk_level", "safe")

        # Destructive tools — never auto-confirm for any role
        if risk == "destructive":
            logger.info(f"Auto-confirm blocked for destructive tool '{tool_name}' (role={role}, source={src})")
            return False

        # Trusted users — only auto-confirm safe tools, not moderate
        if role == DiscordUserRole.TRUSTED and risk != "safe":
            logger.info(f"Auto-confirm blocked for moderate tool '{tool_name}' (role=trusted, source={src})")
            return False

        # Owner — auto-confirm safe + moderate
        return True

    def _strip_live_desktop_context(self, query: str) -> str:
        s = (query or "").strip()
        if not s:
            return ""
        low = s.lower()
        marker = "live desktop context:"
        idx = low.find(marker)
        if idx == -1:
            return s
        return s[:idx].strip()
    # ── User Role Resolution & Role-Based Tool Gating ──────────────────

    # Tools blocked per role. Owner gets everything. Trusted gets most things.
    # Public gets only safe, non-sensitive conversational tools.
    _PUBLIC_BLOCKED_TOOLS: frozenset = frozenset({
        # File system — can leak secrets (.env, credentials, code)
        "file_read", "file_list", "file_write", "file_move", "file_copy",
        "file_delete", "file_mkdir", "artifact_write",
        # Terminal — arbitrary code execution
        "terminal_run",
        # System info — reveals host details
        "system_info",
        # Self-modification — code tampering
        "self_edit", "self_rollback", "self_git_status", "self_read", "self_grep", "self_list",
        # Desktop automation — controls owner's machine
        "desktop_list_windows", "desktop_find_control", "desktop_click",
        "desktop_type_text", "desktop_activate_window", "desktop_send_hotkey",
        "open_chrome", "open_application", "notepad_write",
        # Vision/screen — can see owner's screen
        "analyze_screen", "vision_qa", "take_screenshot",
        # Email — owner's personal email
        "email_read_inbox", "email_search", "email_get_thread", "email_send", "email_reply",
        # Playwright/browser — drives owner's browser session
        "browse_task",
        # Discord personal tools
        "discord_web_send", "discord_web_read_recent",
        "discord_contacts_add", "discord_contacts_discover",
    })

    _TRUSTED_BLOCKED_TOOLS: frozenset = frozenset({
        # Terminal — too dangerous even for trusted users
        "terminal_run",
        # Self-modification — only owner should touch code
        "self_edit", "self_rollback",
        # Desktop/screen — controls owner's machine
        "desktop_click", "desktop_type_text", "desktop_activate_window",
        "desktop_send_hotkey", "open_chrome", "open_application", "notepad_write",
        "analyze_screen", "take_screenshot",
        # Email send — only owner should send emails
        "email_send", "email_reply",
        # Discord personal account tools
        "discord_web_send", "discord_web_read_recent",
        "discord_contacts_add", "discord_contacts_discover",
    })

    def _get_blocked_tools_for_role(self) -> frozenset:
        """Return the set of tool names blocked for the current user role."""
        from config import DiscordUserRole
        role = getattr(self, "_current_user_role", DiscordUserRole.PUBLIC)
        if role == DiscordUserRole.OWNER:
            return frozenset()
        if role == DiscordUserRole.TRUSTED:
            return self._TRUSTED_BLOCKED_TOOLS
        return self._PUBLIC_BLOCKED_TOOLS

    def _is_tool_role_blocked(self, tool_name: str) -> bool:
        """Check if a tool is blocked for the current user's role."""
        try:
            from config import DiscordUserRole
            entry = ToolRegistry.get(tool_name)
            role = getattr(self, "_current_user_role", DiscordUserRole.PUBLIC)
            if entry is not None and entry.category == "mcp" and entry.is_action and role != DiscordUserRole.OWNER:
                return True
        except Exception:
            pass
        return tool_name in self._get_blocked_tools_for_role()

    # ── End Role-Based Tool Gating ───────────────────────────────────

    def _is_action_tool(self, tool_name: str) -> bool:
        return ToolRegistry.is_action(tool_name)

    def _approved_action_matches(
        self,
        tool_name: str,
        kwargs: Optional[Dict[str, Any]] = None,
        approved_action: Optional[Dict[str, Any]] = None,
    ) -> bool:
        action = approved_action or getattr(self, "_active_approved_action", None)
        if not isinstance(action, dict) or str(action.get("tool") or "") != str(tool_name or ""):
            return False
        if kwargs is not None:
            expected = dict(action.get("kwargs") or {})
            if json.dumps(expected, sort_keys=True, default=str) != json.dumps(dict(kwargs or {}), sort_keys=True, default=str):
                return False
        approval_id = str(action.get("approval_id") or "").strip()
        if approval_id:
            try:
                record = self._state_store.get_approval(approval_id)
                accepted = {"approved", "auto_approved"}
                if bool(action.get("_decision_authorized")):
                    accepted.update({"pending", "consuming"})
                if record is None or record.status not in accepted:
                    return False
            except Exception:
                return False
        return self._pending_action_matches_execution_context(action)

    def _constraints_allow_tool(self, tool_name: str, *, approved: bool = False) -> bool:
        authority = getattr(self, "_turn_execution_authority", None)
        constraint_values = (
            authority.constraints
            if bool(getattr(self, "_canonical_semantic_flow", False)) and authority is not None
            else (getattr(self._execution_context, "constraints", []) or [])
        )
        constraints = "\n".join(str(item or "").lower() for item in constraint_values)
        write_tools = {
            "file_write", "file_move", "file_copy", "file_delete", "file_mkdir",
            "artifact_write", "notepad_write", "terminal_run",
            "voice_synthesize_speech", "generation_submit",
        }
        if tool_name in write_tools and any(
            token in constraints
            for token in ("read_only", "read-only", "do not modify", "don't modify", "no_modify", "proposal_only", "proposal only")
        ):
            return False
        if tool_name == "file_delete" and any(token in constraints for token in ("no_delete", "no deletion", "do not delete", "don't delete")):
            return False
        return True

    def _action_configured(self, tool_name: str) -> bool:
        if self._is_tool_role_blocked(tool_name):
            return False
        if tool_name == "open_chrome":
            return bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_open_chrome", False))
        if tool_name == "browse_task":
            return bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_playwright", False))
        if tool_name in {"discord_read_channel", "discord_send_channel"}:
            return bool(getattr(config, "allow_discord_bot", False))
        if tool_name in {"discord_web_send", "discord_contacts_discover"}:
            return bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_playwright", False))
        if tool_name == "open_application":
            return bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_open_application", False))
        if tool_name in {"desktop_click", "desktop_type_text", "desktop_activate_window", "desktop_send_hotkey"}:
            return bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_desktop_automation", False))
        if tool_name == "file_write":
            return bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_file_write", False))
        if tool_name in {"file_move", "file_copy", "file_delete", "file_mkdir", "checkpoint_undo"}:
            return bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_file_write", False))
        if tool_name == "artifact_write":
            return bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_file_write", False))
        if tool_name == "notepad_write":
            return bool(
                getattr(config, "enable_system_actions", False)
                and getattr(config, "allow_open_application", False)
                and getattr(config, "allow_desktop_automation", False)
                and getattr(config, "allow_file_write", False)
            )
        if tool_name == "terminal_run":
            return bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_terminal_commands", False))
        if tool_name in {"email_send", "email_reply"}:
            return bool(getattr(config, "allow_email", False))
        if tool_name == "whatsapp_send":
            return bool(getattr(config, "allow_whatsapp", False))
        if tool_name in {"self_edit", "self_rollback", "self_git_status", "self_read", "self_grep", "self_list"}:
            return bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_self_modification", False))
        entry = ToolRegistry.get(tool_name)
        if entry is not None and entry.category == "mcp":
            if entry.is_action:
                return bool(getattr(config, "enable_system_actions", False))
            return True
        if tool_name == "project_status":
            return True  # Safe read-only tool, always allowed
        # Registry policy flags are the configuration authority for extension
        # actions. Flagless action plugins remain explicit system-action opt-in.
        if entry is not None and entry.is_action:
            return bool(
                self._tool_policy_flags_satisfied(tool_name)
                and (entry.policy_flags or getattr(config, "enable_system_actions", False))
            )
        return False

    def _current_action_authority_allows(self, tool_name: str) -> bool:
        """Fresh non-identity authority check for approval consumption."""
        name = str(tool_name or "").strip()
        if not name or ToolRegistry.get(name) is None:
            return False
        if name not in self._registered_tool_names():
            return False
        if self._is_tool_role_blocked(name):
            return False
        allowed = set(getattr(self._execution_context, "allowed_tool_names", []) or [])
        if name not in allowed:
            return False
        if not self._constraints_allow_tool(name, approved=True):
            return False
        if self._is_action_tool(name) and not self._action_configured(name):
            return False
        return True

    def _action_allowed(
        self,
        tool_name: str,
        kwargs: Optional[Dict[str, Any]] = None,
        approved_action: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if tool_name not in self._registered_tool_names() or ToolRegistry.get(tool_name) is None:
            return False
        approved = self._approved_action_matches(tool_name, kwargs, approved_action)
        if not approved and not self._tool_allowed(tool_name):
            return False
        if not self._constraints_allow_tool(tool_name, approved=approved):
            return False
        return self._action_configured(tool_name)

    def _thread_key(self, thread_id: Optional[str] = None) -> str:
        value = str(thread_id or getattr(self, "_current_thread_id", "default") or "default").strip()
        return value or "default"

    def select_thread_runtime(self, thread_id: Optional[str]) -> str:
        """Select only thread-keyed ephemeral buffers; durable scope comes from StateStore."""
        key = str(thread_id or "default").strip() or "default"
        self._current_thread_id = key
        self.conversation_memory = self._thread_conversation_memories.setdefault(
            key,
            ConversationMemory(),
        )
        if not self.conversation_memory.messages:
            self._rehydrate_conversation_memory(key, self.conversation_memory)
        self._summary = str(self._thread_summaries.get(key, "") or "")
        self._pending_action = None
        state = self._state_store.get_thread_state(key)
        # Project selection is Session-owned. Reset shared-agent residue before
        # any routing or context compilation for the selected Session.
        self._active_project_id = str(state.active_project_id or "").strip() or None
        self._current_subject_text = str(state.current_subject or "")
        self._last_web_query_context = ""
        # Hydrate durable claim for double-check (Session-scoped, not process-global).
        claim_rec = dict(getattr(state, "last_assistant_claim", None) or {})
        self._last_assistant_claim_rec = claim_rec
        self._last_assistant_factual_claim_text = str(claim_rec.get("text") or "")[:400]
        self._last_local_project_path = str(state.project_path or "")
        if not state.project_path:
            self._last_local_project_listing = ""
            self._last_local_project_samples = ""
        return key

    def _rehydrate_conversation_memory(
        self, session_id: str, target: ConversationMemory, *, max_messages: int = 40
    ) -> None:
        """Project durable Session history into a newly created agent buffer."""

        try:
            timeline = self._state_store.session_timeline(session_id, limit=max(1, max_messages // 2 + 4))
            projected: list[Dict[str, str]] = []
            for turn in list(timeline.get("turns") or []):
                for message in list((turn or {}).get("messages") or []):
                    role = str((message or {}).get("role") or "").strip().lower()
                    content = str((message or {}).get("text") or "").strip()
                    if not content or role not in {"user", "assistant"}:
                        continue
                    projected.append({
                        "role": "human" if role == "user" else "ai",
                        "content": content,
                    })
            target.messages = projected[-max(1, int(max_messages or 40)):]
            if target.messages:
                logger.info(
                    "Rehydrated {} conversation message(s) from durable Session {}",
                    len(target.messages),
                    session_id,
                )
        except Exception as exc:
            logger.warning("Durable Session conversation rehydration failed closed: {}", exc)

    def _session_permissions_snapshot(self) -> dict[str, bool]:
        return {
            "system_actions": bool(getattr(config, "enable_system_actions", False)),
            "file_write": bool(getattr(config, "allow_file_write", False)),
            "terminal": bool(getattr(config, "allow_terminal_commands", False)),
            "desktop": bool(getattr(config, "allow_desktop_automation", False)),
            "playwright": bool(getattr(config, "allow_playwright", False)),
            "open_application": bool(getattr(config, "allow_open_application", False)),
            "open_chrome": bool(getattr(config, "allow_open_chrome", False)),
            "email": bool(getattr(config, "allow_email", False)),
            "whatsapp": bool(getattr(config, "allow_whatsapp", False)),
            "discord_bot": bool(getattr(config, "allow_discord_bot", False)),
            "self_modification": bool(getattr(config, "allow_self_modification", False)),
            "voice_actions": bool(getattr(config, "allow_voice_actions", False)),
            "generation_actions": bool(getattr(config, "allow_generation_actions", False)),
        }

    def project_scope_report(self, thread_id: Optional[str] = None) -> dict[str, Any]:
        """Authoritative Session Project scope for capabilities / readiness UIs.

        interaction_mode (chat/research/coding skill workspace) is independent of
        Project attachment. Never report skill-workspace id as the Project name.
        """
        key = self._thread_key(thread_id)
        state = self._state_store.get_thread_state(key)
        project_id = str(state.active_project_id or getattr(self, "_active_project_id", None) or "").strip()
        project_path = str(state.project_path or state.workspace_root or "").strip()
        project_name = ""
        authorized_paths: list[str] = []
        if project_id:
            try:
                from agent.projects import get_project_manager

                project = get_project_manager().get_project(project_id)
                if project is not None:
                    project_name = str(project.name or "").strip()
                    project_path = str(project.workspace_root or project_path or "").strip()
            except Exception:
                pass
        if project_path:
            authorized_paths = [project_path]
        perms = self._session_permissions_snapshot()
        interaction_mode = str(getattr(self, "_workspace_id", None) or state.workspace_id or "").strip() or "chat"
        # skill workspace display name only when no Project is attached
        skill_ws_name = str(getattr(self, "_workspace_name", None) or "").strip()
        return {
            # Do not use "chat" as the Project/workspace identity when a Project is attached.
            "id": project_id or None,
            "name": project_name or ("none" if not project_id else project_id),
            "interaction_mode": interaction_mode,
            "skill_workspace_id": interaction_mode,
            "skill_workspace_name": skill_ws_name or None,
            "project_attached": bool(project_id and project_path),
            "project_id": project_id or None,
            "workspace_name": project_name or "none",
            "project_path": project_path or None,
            "authorized_paths": authorized_paths,
            "permissions": {
                "filesystem_read": bool(project_id and project_path),
                "filesystem_write": bool(project_id and project_path and perms.get("file_write") and perms.get("system_actions")),
                "terminal": bool(project_id and project_path and perms.get("terminal") and perms.get("system_actions")),
                "browser": bool(perms.get("playwright") and perms.get("system_actions")),
                "desktop": bool(perms.get("desktop") and perms.get("system_actions")),
                "system_actions": bool(perms.get("system_actions")),
            },
        }

    def _approval_dry_run_available(self, tool_name: str) -> bool:
        return tool_name in {"desktop_click", "desktop_type_text", "desktop_activate_window", "desktop_send_hotkey"}

    def _approval_risk_metadata(self, tool_name: str) -> tuple[str, list[str]]:
        meta = TOOL_METADATA.get(tool_name, {})
        if not meta:
            entry = ToolRegistry.get(tool_name)
            if entry is not None:
                return str(entry.risk_level or "safe"), list(entry.policy_flags or [])
        return str(meta.get("risk_level", "safe") or "safe"), list(meta.get("policy_flags", []) or [])

    def _normalize_coding_file_path(self, path: str) -> str:
        """Rewrite bare filenames to active Desktop project during coding turns."""
        raw = str(path or "").strip()
        if not raw:
            return raw
        low = raw.replace("\\", "/").lower()
        if low.startswith("desktop/") or Path(raw).is_absolute():
            return raw
        project_path = str(getattr(self._execution_context, "project_path", "") or "").strip()
        if project_path:
            return str(Path(project_path) / raw)
        try:
            from agent.tools import get_active_project_root

            root = get_active_project_root()
            if root is not None:
                return str(root / raw)
        except Exception:
            pass
        return raw

    def _set_pending_action(self, pending: Dict[str, Any], preview: str, user_input: str) -> Dict[str, Any]:
        pending_payload = dict(pending or {})
        tool_name = str(pending_payload.get("tool") or "").strip()
        original_input = str(pending_payload.get("original_input") or user_input or "")
        # Rewrite bare coding paths (index.html → Desktop/<project>/index.html)
        try:
            kw = dict(pending_payload.get("kwargs") or {})
            if tool_name in {"file_write", "file_read", "file_mkdir", "file_delete", "file_list"}:
                if kw.get("path"):
                    kw["path"] = self._normalize_coding_file_path(str(kw.get("path")))
            if tool_name in {"file_move", "file_copy"}:
                if kw.get("src"):
                    kw["src"] = self._normalize_coding_file_path(str(kw.get("src")))
                if kw.get("dst"):
                    kw["dst"] = self._normalize_coding_file_path(str(kw.get("dst")))
            # Named-file pin: refuse write approvals that retarget off the user's file.
            if tool_name == "file_write" and kw.get("path"):
                if not self._file_write_path_allowed_by_request(original_input, str(kw.get("path"))):
                    raise ValueError(
                        f"approval_invalid: write path {Path(str(kw.get('path'))).name} "
                        f"is not the file named in the user request"
                    )
            pending_payload["kwargs"] = kw
            # Refresh preview if path was rewritten
            if tool_name == "file_write" and kw.get("path") and not pending_payload.get("diff_preview"):
                content = kw.get("content") or ""
                preview = f"Write {len(str(content))} chars to file: {kw.get('path')}"
        except ValueError:
            raise
        except Exception:
            pass
        canonical_kwargs = self._canonicalize_tool_arguments(
            tool_name, dict(pending_payload.get("kwargs") or {})
        )
        pending_payload["kwargs"] = canonical_kwargs
        action_id = str(pending_payload.get("action_id") or uuid.uuid4())
        plan_id = str(pending_payload.get("plan_id") or uuid.uuid4())
        arguments_hash = hashlib.sha256(
            json.dumps(canonical_kwargs, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        source_precondition = self._capture_source_precondition(tool_name, canonical_kwargs)
        # Freeze identity fields used at consumption time
        source_precondition = {
            **dict(source_precondition or {}),
            "version": int((source_precondition or {}).get("version") or 1),
            "tool": tool_name,
            "path_basename": Path(str(canonical_kwargs.get("path") or "")).name,
            "original_input_sha256": hashlib.sha256(original_input.encode("utf-8")).hexdigest()[:32],
        }
        pending_payload.update({"action_id": action_id, "plan_id": plan_id})
        risk_level, policy_flags = self._approval_risk_metadata(tool_name)
        active_task = getattr(self, "_active_task_run", None)
        research_binding = dict(getattr(self, "_active_research_binding", None) or {})
        approval = self._state_store.create_approval(
            thread_id=self._thread_key(),
            session_id=self._thread_key(),
            project_id=str(getattr(self, "_active_project_id", None) or ""),
            original_turn_id=str(self._current_execution_id or ""),
            execution_id=self._current_execution_id,
            task_run_id=str(getattr(active_task, "id", "") or ""),
            requirement_id=str(research_binding.get("requirement_id") or ""),
            attempt_id=str(research_binding.get("attempt_id") or ""),
            task_run_revision=int(getattr(active_task, "revision", 0) or 0),
            model_binding_revision=int(
                getattr(
                    getattr(
                        self._state_store.get_thread_state(self._thread_key()),
                        "model_binding",
                        None,
                    ),
                    "binding_revision",
                    0,
                )
                or 0
            ),
            tool=tool_name,
            kwargs=dict(pending_payload.get("kwargs") or {}),
            original_input=str(pending_payload.get("original_input") or user_input or ""),
            preview=preview,
            summary=self._format_pending_action(pending_payload),
            risk_level=risk_level,
            policy_flags=policy_flags,
            session_permissions=self._session_permissions_snapshot(),
            permission_level="modify" if tool_name in AUTOMATION_TOOL_NAMES or self._is_action_tool(tool_name) else "read",
            constraints=list(self._execution_context.constraints or []),
            policy_snapshot={
                "mode": self._execution_context.mode,
                "phase": self._execution_context.phase,
                "allowed_tool_names": list(self._execution_context.allowed_tool_names or []),
                "required_flags": list(TOOL_METADATA.get(tool_name, {}).get("policy_flags", []) or []),
            },
            source_precondition=source_precondition,
            dry_run_available=self._approval_dry_run_available(tool_name),
            source=str(getattr(self, "_current_source", None) or "web"),
            workspace_id=str(self._workspace_id or ""),
            active_project_id=str(getattr(self, "_active_project_id", None) or ""),
            plan_state=pending_payload.get("plan_state") if isinstance(pending_payload.get("plan_state"), dict) else None,
            execution_context={
                "thread_id": self._execution_context.thread_id,
                "workspace_id": self._execution_context.workspace_id,
                "active_project_id": self._execution_context.active_project_id,
                "workspace_root": self._execution_context.workspace_root,
                "project_path": self._execution_context.project_path,
                "objective": self._execution_context.objective,
                "constraints": list(self._execution_context.constraints or []),
                "tool": tool_name,
                "arguments_hash": arguments_hash,
                "action_id": action_id,
                "plan_id": plan_id,
                "origin_execution_id": str(self._current_execution_id or ""),
                "task_run_id": str(getattr(active_task, "id", "") or ""),
                "requirement_id": str(research_binding.get("requirement_id") or ""),
                "attempt_id": str(research_binding.get("attempt_id") or ""),
                "task_run_revision": int(getattr(active_task, "revision", 0) or 0),
                "model_binding_revision": int(
                    getattr(
                        getattr(
                            self._state_store.get_thread_state(self._thread_key()),
                            "model_binding",
                            None,
                        ),
                        "binding_revision",
                        0,
                    )
                    or 0
                ),
                "allowed_tool_names": list(self._execution_context.allowed_tool_names or []),
                "permissions": dict(self._execution_context.permissions or {}),
            },
            action_id=action_id,
            plan_id=plan_id,
            canonical_arguments_hash=arguments_hash,
            required_capabilities=list(self._execution_context.required_capabilities or []),
        )
        pending_payload["approval_id"] = approval.id
        pending_payload["preview"] = preview
        pending_payload["execution_context"] = dict(approval.execution_context or {})
        self._pending_action = pending_payload
        turn_context = self._execution_context
        durable_context = self._state_store.update_thread_state(
            self._thread_key(),
            pending_approval_id=approval.id,
            workspace_id=str(self._workspace_id or ""),
            active_project_id=str(getattr(self, "_active_project_id", None) or ""),
            runtime_provider=self.llm_provider.value,
            execution_status="needs_permission",
            pending_actions=[
                *(self._execution_context.pending_actions or []),
                {"tool": tool_name, "summary": self._format_pending_action(pending_payload),
                 "status": "needs_permission", "execution_id": str(self._current_execution_id or "")},
            ],
            safest_next_action=f"Wait for approval of {tool_name}",
        )
        if bool(getattr(self, "_canonical_semantic_flow", False)):
            projection = durable_context.model_dump()
            for field in (
                "objective", "current_subject", "mode", "phase",
                "required_capabilities", "available_capabilities",
                "allowed_tool_names", "constraints", "decisions",
            ):
                projection[field] = getattr(turn_context, field)
            self._execution_context = ThreadSessionState(**projection)
        else:
            self._execution_context = durable_context
        return pending_payload

    def _path_version(self, target: Path, *, argument: str) -> Dict[str, Any]:
        """Content identity for an approval-bound filesystem path."""
        from agent.tools import _mutation_path_version
        return _mutation_path_version(target, argument)

    def _capture_source_precondition(self, tool_name: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Snapshot every source/destination whose mutation semantics depend on current state."""
        name = str(tool_name or "")
        path_args = {
            "file_write": ("path",),
            "file_delete": ("path",),
            "file_move": ("src", "dst"),
            "file_copy": ("src", "dst"),
            "file_mkdir": ("path",),
        }.get(name, ())
        entries: list[Dict[str, Any]] = []
        if path_args:
            from agent.tools import _safe_file_path
            for argument in path_args:
                raw_path = str((kwargs or {}).get(argument) or "").strip()
                target = _safe_file_path(raw_path)
                if target is None:
                    raise ValueError(f"Cannot bind approval to out-of-scope {argument}: {raw_path}")
                entries.append(self._path_version(target, argument=argument))
        elif name in {"artifact_write", "notepad_write"}:
            from agent.tools import _artifacts_root, _safe_artifact_filename
            target = _artifacts_root() / _safe_artifact_filename((kwargs or {}).get("filename"))
            entries.append(self._path_version(target, argument="filename"))
        elif name == "checkpoint_undo":
            from agent.checkpoints import get_last_checkpoint
            context = self._execution_context
            entry = get_last_checkpoint(self._thread_key(), str(context.project_path or context.workspace_root or ""))
            if entry is None:
                return {"version": 2, "checkpoint": None, "entries": []}
            for argument in ("original_path", "backup_path"):
                entries.append(self._path_version(Path(str(entry.get(argument) or "")), argument=argument))
            return {
                "version": 2,
                "checkpoint": {key: entry.get(key) for key in ("timestamp", "original_path", "backup_path")},
                "entries": entries,
            }
        if not entries:
            return {}
        return {"version": 2, "entries": entries}

    def _source_precondition_matches(self, approval: Any) -> bool:
        precondition = dict(getattr(approval, "source_precondition", None) or {})
        if not precondition:
            return True
        try:
            if int(precondition.get("version") or 1) >= 2:
                current = self._capture_source_precondition(
                    str(getattr(approval, "tool", "") or ""), dict(getattr(approval, "kwargs", None) or {})
                )
                # Compare content identity entries only. Approval freeze metadata
                # (path_basename, original_input_sha256, tool) must not invalidate
                # an unchanged file — those fields are identity aids, not source hashes.
                return json.dumps(current.get("entries") or [], sort_keys=True, separators=(",", ":")) == json.dumps(
                    precondition.get("entries") or [], sort_keys=True, separators=(",", ":")
                )
            # Backward-compatible validation for approvals created before v2.
            target = Path(str(precondition.get("path") or "")).expanduser().resolve(strict=False)
            expected_exists = bool(precondition.get("exists"))
            if target.exists() != expected_exists:
                return False
            if not expected_exists:
                return True
            if not target.is_file():
                return False
            current_hash = hashlib.sha256(target.read_bytes()).hexdigest()
            return current_hash == str(precondition.get("sha256") or "")
        except (OSError, ValueError):
            return False

    def _canonicalize_tool_arguments(self, tool_name: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Return schema-validated arguments used for action identity and execution."""
        raw = next((item for item in getattr(self, "tools", []) if str(getattr(item, "name", "")) == tool_name), None)
        raw = getattr(raw, "_raw_tool", raw)
        entry = ToolRegistry.get(tool_name)
        schema = getattr(raw, "args_schema", None) or getattr(getattr(entry, "func", None), "args_schema", None)
        if schema is None:
            return dict(kwargs or {})
        validated = schema.model_validate(dict(kwargs or {}))
        return validated.model_dump(exclude_none=True)

    def _pending_action_matches_execution_context(self, pending: Dict[str, Any]) -> bool:
        snapshot = dict(pending.get("execution_context") or {})
        if not snapshot:
            # ApprovalRecord is authoritative. Legacy/in-memory pending payloads
            # without frozen identity must be re-prepared, never consumed.
            return False
        current = self._execution_context
        approval_id = str(pending.get("approval_id") or "").strip()
        approval = self._state_store.get_approval(approval_id) if approval_id else None
        accepted_statuses = {"pending"}
        if bool(pending.get("_decision_authorized")):
            accepted_statuses.update({"consuming", "approved", "auto_approved"})
        if approval is None or approval.status not in accepted_statuses:
            return False
        if str(pending.get("action_id") or "") != str(approval.action_id or ""):
            return False
        if str(pending.get("plan_id") or "") != str(approval.plan_id or ""):
            return False
        if str(approval.thread_id or "") != str(current.thread_id or ""):
            return False
        if str(approval.session_id or "") != str(current.session_id or current.thread_id or ""):
            return False
        if str(approval.project_id or "") != str(current.active_project_id or ""):
            return False
        if str(approval.active_project_id or "") != str(current.active_project_id or ""):
            return False
        # ProjectManager is the metadata/root authority. Re-read it at the
        # consumption boundary rather than trusting ThreadSessionState's cache.
        current_project_id = str(current.active_project_id or "").strip()
        if current_project_id:
            try:
                from agent.projects import get_project_manager
                project = get_project_manager().get_project(current_project_id)
                metadata = dict(getattr(project, "metadata", None) or {}) if project is not None else {}
                project_root = str(
                    getattr(project, "workspace_root", "")
                    or metadata.get("project_path")
                    or metadata.get("workspace_root")
                    or metadata.get("path")
                    or ""
                ).strip()
                if project is None or not project_root or os.path.normcase(os.path.abspath(project_root)) != os.path.normcase(
                    os.path.abspath(str(current.project_path or current.workspace_root or ""))
                ):
                    return False
            except Exception:
                return False
        origin_execution_id = str(snapshot.get("origin_execution_id") or "").strip()
        if origin_execution_id and origin_execution_id != str(approval.execution_id or ""):
            return False
        if str(snapshot.get("thread_id") or "") != current.thread_id:
            return False
        snap_project = str(snapshot.get("project_path") or "").strip()
        if snap_project:
            try:
                if os.path.normcase(os.path.abspath(snap_project)) != os.path.normcase(
                    os.path.abspath(str(current.project_path or "").strip())
                ):
                    return False
            except (OSError, ValueError):
                return False
        snap_workspace = str(snapshot.get("workspace_root") or "").strip()
        if snap_workspace:
            try:
                if os.path.normcase(os.path.abspath(snap_workspace)) != os.path.normcase(
                    os.path.abspath(str(current.workspace_root or "").strip())
                ):
                    return False
            except (OSError, ValueError):
                return False
        snap_project_id = str(snapshot.get("active_project_id") or "").strip()
        if snap_project_id and snap_project_id != str(current.active_project_id or "").strip():
            return False
        snap_tool = str(snapshot.get("tool") or "").strip()
        if snap_tool and snap_tool != str(pending.get("tool") or "").strip():
            return False
        args_hash = str(approval.canonical_arguments_hash or snapshot.get("arguments_hash") or "").strip()
        if args_hash:
            try:
                canonical = self._canonicalize_tool_arguments(
                    str(pending.get("tool") or ""), dict(pending.get("kwargs") or {})
                )
            except Exception:
                return False
            current_hash = hashlib.sha256(
                json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
            ).hexdigest()
            if args_hash != current_hash:
                return False
        if not self._current_action_authority_allows(str(pending.get("tool") or "")):
            return False
        if not self._source_precondition_matches(approval):
            return False
        # Frozen path basename: reject if pending kwargs drifted to another file.
        precondition = dict(getattr(approval, "source_precondition", None) or {})
        frozen_base = str(precondition.get("path_basename") or "").casefold()
        if frozen_base and str(pending.get("tool") or "") in {"file_write", "file_read", "file_delete"}:
            current_base = Path(str((pending.get("kwargs") or {}).get("path") or "")).name.casefold()
            if current_base and current_base != frozen_base:
                return False
        # Session / Project identity on the ApprovalRecord itself
        if str(getattr(approval, "session_id", "") or "") and str(approval.session_id) != str(current.thread_id or ""):
            return False
        if str(getattr(approval, "project_id", "") or "") and str(approval.project_id) != str(
            current.active_project_id or ""
        ):
            return False
        if str(getattr(approval, "active_project_id", "") or "") and str(approval.active_project_id) != str(
            current.active_project_id or ""
        ):
            return False
        # Mutable policy/permission snapshots are audit evidence, not action
        # identity. Revalidate the current authority directly instead of
        # canceling an unchanged action because metadata was refreshed.
        if not self._constraints_allow_tool(str(pending.get("tool") or ""), approved=True):
            return False
        return True

    def _capability_registry(self) -> dict[str, dict[str, Any]]:
        """Machine-readable capabilities derived from the real registered inventory."""
        registered = self._registered_tool_names()
        specs: dict[str, dict[str, Any]] = {
            "research": {
                "supported_task": "Gather and synthesize current evidence",
                "required_tools": ["web_search"],
                "preconditions": ["A concrete research question"],
                "permissions": [],
                "configuration": ["At least one search provider"],
                "limitations": ["Read-only; source quality can limit conclusions"],
                "composes_with": ["coding", "conversation"],
            },
            "filesystem_read": {
                "supported_task": "Inspect project files and directories",
                "required_tools": ["file_list", "file_read"],
                "preconditions": ["A thread workspace or project root"],
                "permissions": [],
                "configuration": ["FILE_TOOL_ROOT or an active project"],
                "limitations": ["Restricted to the current thread scope"],
                "composes_with": ["coding", "research"],
            },
            "filesystem_write": {
                "supported_task": "Create or modify project files",
                "required_tools": ["file_write"],
                "preconditions": ["A thread project root", "Confirmation when required"],
                "permissions": ["system_actions", "file_write"],
                "configuration": [],
                "limitations": ["Cannot write outside the thread project scope"],
                "composes_with": ["filesystem_read", "verification"],
            },
            "terminal": {
                "supported_task": "Run project-local verification or build commands",
                "required_tools": ["terminal_run"],
                "preconditions": ["A thread project root", "Confirmation when required"],
                "permissions": ["system_actions", "terminal"],
                "configuration": [],
                "limitations": ["Command chaining is rejected; cwd is thread-scoped"],
                "composes_with": ["coding", "verification"],
            },
            "conversation": {
                "supported_task": "Answer and maintain conversational continuity",
                "required_tools": [],
                "preconditions": [],
                "permissions": [],
                "configuration": [],
                "limitations": ["Current facts require research"],
                "composes_with": ["research", "coding"],
            },
        }
        permissions = self._session_permissions_snapshot()
        for name, spec in specs.items():
            required = list(spec.get("required_tools") or [])
            installed = all(tool in registered for tool in required)
            configured = all(bool(permissions.get(flag, False)) for flag in spec.get("permissions") or [])
            spec["installed"] = installed
            spec["configured"] = configured
            spec["status"] = (
                "direct" if not required else "tool_supported" if installed and configured
                else "blocked_configuration" if installed else "unsupported"
            )
        return specs

    def _update_thread_progress_preserving_turn_authority(self, **changes: Any) -> ThreadSessionState:
        """Persist progress without replacing the active Turn's authority view."""

        prior = self._execution_context
        durable = self._state_store.update_thread_state(self._thread_key(), **changes)
        authority = getattr(self, "_turn_execution_authority", None)
        if not bool(getattr(self, "_canonical_semantic_flow", False)) or authority is None:
            self._execution_context = durable
            return durable
        ephemeral = durable.model_dump()
        ephemeral.update({
            "objective": str(getattr(prior, "objective", "") or ""),
            "current_subject": str(getattr(prior, "current_subject", "") or ""),
            "mode": authority.mode,
            "phase": str(getattr(prior, "phase", "") or ""),
            "required_capabilities": list(getattr(prior, "required_capabilities", []) or []),
            "available_capabilities": list(getattr(prior, "available_capabilities", []) or []),
            "allowed_tool_names": sorted(authority.allowed_tool_names),
            "constraints": sorted(authority.constraints),
            "decisions": list(getattr(prior, "decisions", []) or []),
        })
        self._execution_context = ThreadSessionState.model_validate(ephemeral)
        from agent.tools import update_tool_execution_context
        update_tool_execution_context(
            thread_id=self._execution_context.thread_id,
            workspace_root=self._execution_context.workspace_root,
            project_root=self._execution_context.project_path,
            allowed_tool_names=self._execution_context.allowed_tool_names,
            permissions=self._execution_context.permissions,
            execution_id=self._current_execution_id or "",
            enforce_tools=True,
            strict_scope=True,
        )
        return self._execution_context

    def _record_ledger_entry(self, **payload: Any) -> ProjectLedgerEntry:
        payload.setdefault("project_path", str(self._execution_context.project_path or ""))
        payload.setdefault("objective", str(self._execution_context.objective or ""))
        payload.setdefault("execution_id", str(self._current_execution_id or ""))
        entry = self._state_store.add_ledger_entry(self._thread_key(), **payload)
        self._execution_context = self._state_store.get_thread_state(self._thread_key())
        return entry

    def completed_execution_id_for_current_worker(self) -> str:
        """Return this worker thread's just-finished Turn, immune to later Session work."""
        return str(getattr(self._request_result_local, "execution_id", "") or "")

    def _format_pending_action(self, pending: Dict[str, Any]) -> str:
        name = pending.get("tool") or ""
        kwargs = pending.get("kwargs") or {}
        if name == "open_chrome":
            url = (kwargs or {}).get("url")
            if url:
                return f"Open Chrome and navigate to: {url}"
            return "Open Google Chrome"
        if name == "open_application":
            app = (kwargs or {}).get("app")
            args = (kwargs or {}).get("args")
            if app and args:
                return f"Open application: {app} (args: {args})"
            if app:
                return f"Open application: {app}"
            return "Open an application"
        if name == "browse_task":
            url = (kwargs or {}).get("url")
            task = (kwargs or {}).get("task")
            if url and task:
                return f"Browse: {url} (task: {task})"
            if url:
                return f"Browse: {url}"
            return "Browse a website"
        if name == "desktop_click":
            window_title = (kwargs or {}).get("window_title")
            control_name = (kwargs or {}).get("control_name")
            automation_id = (kwargs or {}).get("automation_id")
            control_type = (kwargs or {}).get("control_type")
            parts = []
            if window_title:
                parts.append(f"window={window_title}")
            if control_name:
                parts.append(f"control_name={control_name}")
            if automation_id:
                parts.append(f"automation_id={automation_id}")
            if control_type:
                parts.append(f"control_type={control_type}")
            return "Desktop click (" + ", ".join(parts) + ")" if parts else "Desktop click"
        if name == "desktop_type_text":
            window_title = (kwargs or {}).get("window_title")
            control_name = (kwargs or {}).get("control_name")
            automation_id = (kwargs or {}).get("automation_id")
            control_type = (kwargs or {}).get("control_type")
            text = (kwargs or {}).get("text")
            preview = (text or "")
            if isinstance(preview, str) and len(preview) > 60:
                preview = preview[:60].rstrip() + "…"
            parts = []
            if window_title:
                parts.append(f"window={window_title}")
            if control_name:
                parts.append(f"control_name={control_name}")
            if automation_id:
                parts.append(f"automation_id={automation_id}")
            if control_type:
                parts.append(f"control_type={control_type}")
            if preview:
                parts.append(f"text={preview}")
            return "Desktop type (" + ", ".join(parts) + ")" if parts else "Desktop type"
        if name == "desktop_activate_window":
            window_title = (kwargs or {}).get("window_title")
            if window_title:
                return f"Activate window: {window_title}"
            return "Activate a window"
        if name == "desktop_send_hotkey":
            window_title = (kwargs or {}).get("window_title")
            hotkey = (kwargs or {}).get("hotkey")
            if window_title and hotkey:
                return f"Send hotkey {hotkey} to window: {window_title}"
            if hotkey:
                return f"Send hotkey: {hotkey}"
            return "Send a hotkey"
        if name == "file_write":
            path = (kwargs or {}).get("path")
            content = (kwargs or {}).get("content") or ""
            append = (kwargs or {}).get("append") is True
            preview = f"{len(str(content))} chars" if content is not None else "content"
            if path:
                suffix = " (append)" if append else ""
                return f"Write {preview} to file: {path}{suffix}"
            return "Write to a file"
        if name == "file_move":
            src = (kwargs or {}).get("src")
            dst = (kwargs or {}).get("dst")
            overwrite = (kwargs or {}).get("overwrite") is True
            suffix = " (overwrite)" if overwrite else ""
            if src and dst:
                return f"Move: {src} -> {dst}{suffix}"
            return "Move a file/folder"
        if name == "file_copy":
            src = (kwargs or {}).get("src")
            dst = (kwargs or {}).get("dst")
            overwrite = (kwargs or {}).get("overwrite") is True
            suffix = " (overwrite)" if overwrite else ""
            if src and dst:
                return f"Copy: {src} -> {dst}{suffix}"
            return "Copy a file/folder"
        if name == "file_delete":
            path = (kwargs or {}).get("path")
            recursive = (kwargs or {}).get("recursive") is True
            suffix = " (recursive)" if recursive else ""
            if path:
                return f"Delete: {path}{suffix}"
            return "Delete a file/folder"
        if name == "file_mkdir":
            path = (kwargs or {}).get("path")
            if path:
                return f"Create folder: {path}"
            return "Create a folder"
        if name == "artifact_write":
            filename = (kwargs or {}).get("filename")
            content = (kwargs or {}).get("content") or ""
            preview = f"{len(str(content))} chars" if content is not None else "content"
            if filename:
                return f"Write {preview} to artifact: {filename}"
            return f"Write {preview} to an artifact file"
        if name == "terminal_run":
            command = (kwargs or {}).get("command") or ""
            cwd = (kwargs or {}).get("cwd")
            preview = str(command)
            if isinstance(preview, str) and len(preview) > 90:
                preview = preview[:90].rstrip() + "…"
            if cwd:
                return f"Run terminal command (cwd={cwd}): {preview}"
            return f"Run terminal command: {preview}"
        if name == "notepad_write":
            filename = (kwargs or {}).get("filename")
            content = (kwargs or {}).get("content") or ""
            preview = f"{len(str(content))} chars" if content is not None else "content"
            if filename:
                return f"Open Notepad, type {preview}, and save artifact: {filename}"
            return f"Open Notepad and type {preview}"
        if name == "discord_send_channel":
            channel = (kwargs or {}).get("channel") or ""
            message = (kwargs or {}).get("message") or ""
            msg_preview = str(message)
            if len(msg_preview) > 200:
                msg_preview = msg_preview[:200].rstrip() + "…"
            if channel and msg_preview:
                return f"Post to Discord channel #{channel}: {msg_preview}"
            if channel:
                return f"Post to Discord channel #{channel}"
            return "Post to a Discord channel"
        if name == "discord_web_send":
            recipient = (kwargs or {}).get("recipient") or ""
            message = (kwargs or {}).get("message") or ""
            msg_preview = str(message)
            if len(msg_preview) > 200:
                msg_preview = msg_preview[:200].rstrip() + "…"
            if recipient and msg_preview:
                return f"Send Discord DM to {recipient}: {msg_preview}"
            if recipient:
                return f"Send Discord DM to {recipient}"
            return "Send a Discord DM"
        return f"Run tool: {name}"

    def _has_vision_intent(self, query_lower: str, has_monitor_ctx: bool = False) -> bool:
        q = (query_lower or "").strip()
        if not q:
            return False

        file_nouns = ["file", "files", "folder", "folders", "directory", "directories"]
        file_verbs = ["create", "make", "new", "mkdir", "list", "show", "move", "copy", "delete", "remove", "rename"]
        if any(n in q for n in file_nouns) and any(v in q for v in file_verbs):
            return False

        strong_phrases = [
            "what do you see",
            "what am i looking at",
            "look at my screen",
            "on my screen",
            "describe the screen",
            "describe what's on",
        ]
        if any(p in q for p in strong_phrases):
            return True

        visual_nouns = [
            "video",
            "clip",
            "screen",
            "desktop",
            "monitor",
            "window",
            "tab",
            "page",
            "image",
            "picture",
            "photo",
            "screenshot",
        ]
        has_visual_noun = any(n in q for n in visual_nouns)

        deictic = ["this", "that", "here", "right here", "there"]
        has_deictic = any(d in q for d in deictic)

        visual_verbs = ["look", "see", "watch", "check", "show", "identify", "describe"]
        has_visual_verb = any(v in q for v in visual_verbs)

        if "check this out" in q and (has_visual_noun or has_monitor_ctx):
            return True
        if "look at this" in q and (has_visual_noun or has_monitor_ctx):
            return True
        if "watch this" in q and ("video" in q or "clip" in q or has_monitor_ctx):
            return True

        if ("what is this" in q or "what's this" in q or "what is that" in q or "what's that" in q) and (
            "video" in q or "clip" in q or "screen" in q or "desktop" in q or has_monitor_ctx
        ):
            return True

        if ("in this video" in q or "in the video" in q or "in this clip" in q or "in the clip" in q) and (
            has_deictic or has_visual_verb
        ):
            return True

        if has_visual_noun and (has_visual_verb or has_deictic):
            return True

        return False

    def _find_tool(self, query: str) -> Optional[Tool]:
        query_lower_full = (query or "").lower()
        query_main = self._strip_live_desktop_context(query)
        query_main = self._extract_user_request_text(query_main)
        query_lower = query_main.lower()

        has_monitor_ctx = "live desktop context" in query_lower_full

        # If the UI attached live desktop context (monitor mode), prefer the vision model.
        if has_monitor_ctx and self._has_vision_intent(query_lower, has_monitor_ctx=True):
            for tool in self.tools:
                if tool.name == "vision_qa":
                    return tool
        for tool in self.tools:
            if tool.name.replace("_", " ") in query_lower:
                return tool
            if "search" in query_lower and tool.name == "web_search":
                preferred = self._preferred_web_research_tool()
                if preferred is not None:
                    return preferred
            if ("youtube" in query_lower or "youtu.be" in query_lower or "youtube.com" in query_lower) and tool.name == "youtube_transcript":
                return tool
            if any(kw in query_lower for kw in ["browse", "read this site", "read this page", "summarize this site", "summarize this page", "open this site", "open this page"]) and tool.name == "browse_task":
                return tool
            if self._is_direct_time_question(query_lower) and tool.name == "get_system_time":
                return tool
            if any(kw in query_lower for kw in ["calculate", "compute", "evaluate", "solve", "plus", "minus", "multiply", "divide"]) and tool.name == "calculate":
                return tool
            if self._has_vision_intent(query_lower, has_monitor_ctx=has_monitor_ctx) and tool.name == "vision_qa":
                return tool
        tool_indicators = {
            "web_search": [
                "right now",
                "currently",
                "today",
                "live",
                "score",
                "scores",
                "weather",
                "forecast",
                "price",
                "stock",
                "stocks",
                "bitcoin",
                "btc",
                "ethereum",
                "eth",
                "flight status",
                "traffic",
                "availability",
                "latest",
                "headlines",
                "current events",
                "top stories",
                "breaking news",
                "latest news",
                "recent news",
                "news about",
                "search",
                "look up",
                "find out",
                "updates on",
                "update on",
                "latest update",
            ],
            "get_system_time": ["what time is it", "time is it", "current time", "what date", "what's the date", "today's date", "todays date", "current date", "date today"],
            "calculate": ["calculate", "compute", "evaluate", "solve", "times", "equals"],
            "system_info": ["system info", "specs", "hardware", "gpu", "vram", "ram", "cpu", "my pc", "my computer", "my laptop"],
            "analyze_screen": ["screen", "what's on", "display", "visible", "ocr", "read what's"],
            "youtube_transcript": ["transcript", "caption", "captions", "subtitles", "youtube transcript"],
            "browse_task": ["browse", "read this site", "read this page", "summarize this site", "summarize this page", "open this site", "open this page"],
            "desktop_list_windows": ["list windows", "what windows", "open windows", "which windows"],
            "desktop_find_control": ["find control", "find button", "find textbox", "find text box", "find element"],
            "desktop_click": ["desktop click", "click in", "click on"],
            "desktop_type_text": ["desktop type", "type in", "type into", "enter text"],
            "desktop_activate_window": ["activate window", "focus window", "bring to front"],
            "desktop_send_hotkey": ["send hotkey", "press hotkey", "press ctrl", "press alt", "press win"],
            "file_list": ["list files", "list folder", "show files", "show folder", "list directory", "browse files"],
            "file_read": ["read file", "open file", "show file", "view file", "file contents"],
            "file_write": ["write file", "save file", "append file", "write to file", "write ", "save ", "append to", "create file", "new file"],
            "file_move": ["move file", "rename file", "move folder", "rename folder"],
            "file_copy": ["copy file", "copy folder", "duplicate file", "duplicate folder"],
            "file_delete": ["delete file", "remove file", "delete folder", "remove folder"],
            "file_mkdir": [
                "create folder",
                "create a folder",
                "create a new folder",
                "make folder",
                "make a folder",
                "new folder",
                "new folder called",
                "new folder named",
                "folder called",
                "folder named",
                "mkdir",
                "create directory",
                "create a directory",
                "make directory",
                "make a directory",
            ],
            "terminal_run": [
                "run command",
                "execute command",
                "terminal run",
                "run in terminal",
                "powershell:",
                "cmd:",
                "ps:",
                "run ",
                "execute ",
                "command ",
                "terminal ",
            ],
            "vision_qa": [
                "what am i looking at",
                "what do you see",
                "look at my screen",
                "on my screen",
                "on my desktop",
                "describe the screen",
                "describe what's on",
            ],
            "open_chrome": [
                "open chrome",
                "launch chrome",
                "start chrome",
                "open google chrome",
                "open browser",
                "launch browser",
            ],
            "open_application": [
                "open notepad",
                "launch notepad",
                "start notepad",
                "open calculator",
                "launch calculator",
                "open calc",
                "launch calc",
                "open paint",
                "launch paint",
                "open explorer",
                "open file explorer",
                "launch explorer",
                "open command prompt",
                "open cmd",
                "open powershell",
                "open terminal",
            ],
            "self_edit": [
                "edit your own",
                "edit my own",
                "modify your",
                "modify my",
                "change your code",
                "change my code",
                "fix your bug",
                "fix my bug",
                "fix the bug",
                "fix a bug",
                "add a tool",
                "add a new tool",
                "create a tool",
                "self edit",
                "self-edit",
                "improve your",
                "update your code",
                "patch your",
                "soul.md",
                "your soul",
                "fix your soul",
                "edit your soul",
                "update your soul",
                "trim your soul",
            ],
            "self_rollback": [
                "rollback",
                "roll back",
                "undo your changes",
                "undo my changes",
                "revert your",
                "revert my",
                "restore previous",
                "go back to before",
            ],
            "project_update_context": [
                "what changed",
                "what did you change",
                "show changes",
                "show your changes",
                "what's new",
                "whats new",
                "new updates",
                "recent updates",
                "latest updates",
                "changelog",
                "what have you been working on",
                "what did you build",
                "what did you ship",
            ],
            "self_git_status": [
                "git status",
                "show git",
                "show git status",
                "repo status",
                "git log",
            ],
            "self_read": [
                "read your",
                "read my",
                "show me your code",
                "show me the code",
                "what's in your",
                "what is in your",
                "look at your",
                "open your code",
            ],
            "self_grep": [
                "search your code",
                "search your files",
                "find in your code",
                "grep your",
                "where is",
                "where do you",
            ],
            "self_list": [
                "list your files",
                "show your files",
                "what files do you have",
                "show me your project",
                "list your codebase",
            ],
        }

        # Discord server-channel routing (single source of truth)
        dc_intent = self._detect_discord_channel_intent(query_main)
        if dc_intent.get("kind") == "post":
            for tool in self.tools:
                if tool.name == "discord_send_channel" and self._tool_allowed(tool.name):
                    return tool
        if dc_intent.get("kind") == "recap":
            for tool in self.tools:
                if tool.name == "discord_read_channel" and self._tool_allowed(tool.name):
                    return tool

        has_discord_keyword = "discord" in query_lower

        # DMs/personal: only route to Playwright tools when the user explicitly references Discord.
        if has_discord_keyword and ("read" in query_lower or "check" in query_lower or "messages" in query_lower):
            for tool in self.tools:
                if tool.name == "discord_web_read_recent" and self._tool_allowed(tool.name):
                    return tool

        if self._is_direct_time_question(query_lower):
            for tool in self.tools:
                if tool.name == "get_system_time" and self._tool_allowed(tool.name):
                    return tool

        if self._is_hardware_capability_query(query_lower):
            for tool in self.tools:
                if tool.name == "system_info" and self._tool_allowed(tool.name):
                    return tool

        if self._is_schedule_time_query(query_lower):
            preferred = self._preferred_web_research_tool()
            if preferred is not None:
                return preferred

        if self._is_live_web_intent(query_lower):
            for tool in self.tools:
                if tool.name == "web_search" and self._tool_allowed(tool.name):
                    return tool

        if self._has_vision_intent(query_lower, has_monitor_ctx=has_monitor_ctx):
            for tool in self.tools:
                if tool.name == "vision_qa" and self._tool_allowed(tool.name):
                    return tool

        yt_url = self._extract_youtube_url(query_main)
        if yt_url:
            for tool in self.tools:
                if tool.name == "youtube_transcript" and self._tool_allowed(tool.name):
                    return tool

        creator_queries = self._creator_search_queries(query_main)
        if creator_queries:
            preferred = self._preferred_web_research_tool()
            if preferred is not None:
                return preferred

        browse_url = self._extract_url(query_main)
        if browse_url and any(x in query_lower for x in tool_indicators["browse_task"]):
            for tool in self.tools:
                if tool.name == "browse_task" and self._tool_allowed(tool.name):
                    return tool

        if any(x in query_lower for x in tool_indicators["desktop_list_windows"]):
            for tool in self.tools:
                if tool.name == "desktop_list_windows" and self._tool_allowed(tool.name):
                    return tool

        if any(x in query_lower for x in tool_indicators["desktop_find_control"]):
            for tool in self.tools:
                if tool.name == "desktop_find_control" and self._tool_allowed(tool.name):
                    return tool

        if ("click" in query_lower) and any(x in query_lower for x in ("window", "app", "desktop", " in ")):
            for tool in self.tools:
                if tool.name == "desktop_click" and self._tool_allowed(tool.name):
                    return tool

        if ("type" in query_lower or "enter" in query_lower) and any(x in query_lower for x in ("window", "app", "desktop", " into ", " in ")):
            for tool in self.tools:
                if tool.name == "desktop_type_text" and self._tool_allowed(tool.name):
                    return tool

        if any(x in query_lower for x in tool_indicators["desktop_activate_window"]):
            for tool in self.tools:
                if tool.name == "desktop_activate_window" and self._tool_allowed(tool.name):
                    return tool

        if any(x in query_lower for x in tool_indicators["desktop_send_hotkey"]):
            for tool in self.tools:
                if tool.name == "desktop_send_hotkey" and self._tool_allowed(tool.name):
                    return tool

        if any(x in query_lower for x in tool_indicators["file_list"]):
            for tool in self.tools:
                if tool.name == "file_list" and self._tool_allowed(tool.name):
                    return tool

        if any(x in query_lower for x in tool_indicators["file_read"]):
            for tool in self.tools:
                if tool.name == "file_read" and self._tool_allowed(tool.name):
                    return tool

        # Self-modification tools — map to actual file tools since self_* were never implemented
        if any(x in query_lower for x in tool_indicators.get("self_edit", [])):
            for tool in self.tools:
                if tool.name == "file_write" and self._tool_allowed(tool.name):
                    return tool

        if any(x in query_lower for x in tool_indicators.get("self_rollback", [])):
            for tool in self.tools:
                if tool.name == "self_rollback" and self._tool_allowed(tool.name):
                    return tool

        if any(x in query_lower for x in tool_indicators.get("project_update_context", [])):
            for tool in self.tools:
                if tool.name == "project_update_context" and self._tool_allowed(tool.name):
                    return tool

        if any(x in query_lower for x in tool_indicators.get("self_git_status", [])):
            for tool in self.tools:
                if tool.name == "self_git_status" and self._tool_allowed(tool.name):
                    return tool

        if any(x in query_lower for x in tool_indicators.get("self_read", [])):
            for tool in self.tools:
                if tool.name == "file_read" and self._tool_allowed(tool.name):
                    return tool

        if any(x in query_lower for x in tool_indicators.get("self_grep", [])):
            for tool in self.tools:
                if tool.name == "file_list" and self._tool_allowed(tool.name):
                    return tool

        if any(x in query_lower for x in tool_indicators.get("self_list", [])):
            for tool in self.tools:
                if tool.name == "file_list" and self._tool_allowed(tool.name):
                    return tool

        if any(x in query_lower for x in tool_indicators["file_write"]) or re.search(r"\b(?:create|make)\s+(?:a\s+)?file\b", query_lower):
            for tool in self.tools:
                if tool.name == "file_write" and self._tool_allowed(tool.name):
                    return tool

        if any(x in query_lower for x in tool_indicators["file_move"]):
            for tool in self.tools:
                if tool.name == "file_move" and self._tool_allowed(tool.name):
                    return tool

        if any(x in query_lower for x in tool_indicators["file_copy"]):
            for tool in self.tools:
                if tool.name == "file_copy" and self._tool_allowed(tool.name):
                    return tool

        if any(x in query_lower for x in tool_indicators["file_delete"]):
            for tool in self.tools:
                if tool.name == "file_delete" and self._tool_allowed(tool.name):
                    return tool

        if any(x in query_lower for x in tool_indicators["file_mkdir"]):
            for tool in self.tools:
                if tool.name == "file_mkdir" and self._tool_allowed(tool.name):
                    return tool

        if any(x in query_lower for x in tool_indicators.get("open_application") or []):
            for tool in self.tools:
                if tool.name == "open_application" and self._tool_allowed(tool.name):
                    return tool

        # Guard: Discord URLs / discord tool names should not trigger terminal heuristics.
        discord_like = "discord.com/channels" in query_lower or "discord_web_" in query_lower

        if (not discord_like) and any(x in query_lower for x in tool_indicators["terminal_run"]):
            for tool in self.tools:
                if tool.name == "terminal_run" and self._tool_allowed(tool.name):
                    return tool

        calc_keywords = tool_indicators["calculate"]
        has_calc_keyword = any(ind in query_lower for ind in calc_keywords)
        has_math_operator = bool(re.search(r"\d\s*[+\-*/^]\s*\d", query_lower))
        if has_calc_keyword or has_math_operator:
            for tool in self.tools:
                if tool.name == "calculate" and self._tool_allowed(tool.name):
                    return tool

        for tool_name, indicators in tool_indicators.items():
            if any(ind in query_lower for ind in indicators):
                for tool in self.tools:
                    if tool.name == tool_name and self._tool_allowed(tool.name):
                        return tool
        return None

    # ------------------------------------------------------------------
    # Structured intent routing (Phase 2 bridge)
    # ------------------------------------------------------------------

    def _extract_url(self, user_input: str) -> Optional[str]:
        text = (user_input or "").strip()
        m = re.search(r"(https?://\S+|www\.[^\s]+)", text, flags=re.IGNORECASE)
        if m:
            return m.group(1).rstrip(").,;\"]")

        low = text.lower()
        phrases = [
            "go to ",
            "visit ",
            "navigate to ",
        ]
        for ph in phrases:
            idx = low.find(ph)
            if idx == -1:
                continue
            tail = text[idx + len(ph):].strip()
            if not tail:
                continue
            # stop at conjunctions like "and" to avoid capturing the whole sentence
            tail = re.split(r"\b(and|then)\b", tail, maxsplit=1, flags=re.IGNORECASE)[0].strip()
            if not tail:
                continue
            token = tail.split()[0].strip("\"'()[]{}<>")
            token = token.rstrip(".,;!?")
            if token:
                return token

        m2 = re.search(
            r"\b(?:open|launch|start)\s+(?:google\s+)?chrome\b(?:\s+(?:and\s+)?)?(?:go\s+to\s+|visit\s+|navigate\s+to\s+)?(?P<target>\S+)",
            text,
            flags=re.IGNORECASE,
        )
        if m2:
            token = (m2.group("target") or "").strip("\"'()[]{}<>")
            token = token.rstrip(".,;!?")
            if token.lower() in ("chrome", "browser"):
                return None
            return token

        return None

    def _extract_youtube_url(self, user_input: str) -> Optional[str]:
        text = (user_input or "").strip()
        m = re.search(r"(https?://\S+|www\.[^\s]+)", text, flags=re.IGNORECASE)
        if not m:
            return None
        url = m.group(1).rstrip(").,;\"]")
        low = url.lower()
        if "youtube.com" in low or "youtu.be" in low:
            if url.startswith("www."):
                return "https://" + url
            return url
        return None

    def _emit_tool_start(
        self,
        callbacks: Optional[list],
        name: str,
        input_str: str,
        run_id: str,
        *,
        notify_callbacks: bool = True,
        ensure_durable: bool = True,
    ) -> str:
        # Track tool start time for observability latency measurement
        if not hasattr(self, '_tool_start_times'):
            self._tool_start_times = {}
        rid = str(run_id or "").strip() or str(uuid.uuid4())
        tool_name = str(name or "").strip() or "tool"
        self._tool_start_times[rid] = time.time()
        # Map run_id → tool name for _emit_tool_end to look up
        self._partial_tool_names[rid] = tool_name
        if not hasattr(self, "_partial_tool_inputs"):
            self._partial_tool_inputs = {}
        self._partial_tool_inputs[rid] = str(input_str or "")
        # Register BEFORE callbacks / invoke so ToolOutcome.run_id matches stream id.
        self._register_tool_run(tool_name, rid)
        if ensure_durable:
            self._ensure_durable_tool_run_started(tool_name, rid, str(input_str or ""))

        # Stream event (fire-and-forget)
        if hasattr(self, '_stream_buffer') and self._stream_buffer:
            try:
                safe_preview = re.sub(
                    r"(?i)(api[_ -]?key|password|token|secret|credential)\s*[:=]\s*\S+",
                    r"\1=[redacted]",
                    str(input_str or ""),
                )[:600]
                self._stream_buffer.push_tool_start(tool_name, rid, {"input_preview": safe_preview})
            except Exception:
                pass

        if not callbacks:
            return rid
        if notify_callbacks:
            serialized = {"name": tool_name}
            for cb in callbacks:
                fn = getattr(cb, "on_tool_start", None)
                if callable(fn):
                    try:
                        fn(serialized, input_str, rid)
                    except Exception:
                        pass
        else:
            # Fan-out rows: put UI events without re-entering LC on_tool_start (avoids dual outer/inner).
            safe_in = re.sub(r"\s+", " ", str(input_str or "")).strip()
            if len(safe_in) > 600:
                safe_in = safe_in[:600] + "…"
            for cb in callbacks:
                q = getattr(cb, "_q", None)
                if q is None:
                    continue
                try:
                    q.put({
                        "type": "tool_start",
                        "id": rid,
                        "name": tool_name,
                        "input": safe_in,
                        "at": time.time(),
                        "request_id": str(getattr(self, "_current_request_id", "") or getattr(cb, "_request_id", "") or ""),
                    })
                except Exception:
                    pass
        return rid

    def _ensure_durable_tool_run_started(self, tool_name: str, run_id: str, tool_input: str = "") -> None:
        """Create a durable ToolRun at stream start so chat rows always have matching history."""
        rid = str(run_id or "").strip()
        if not rid:
            return
        turn_id = str(getattr(self, "_current_execution_id", "") or "")
        if not turn_id:
            return
        try:
            existing = self._state_store.list_tool_runs(turn_id)
            if any(run.id == rid for run in existing):
                return
            args: Dict[str, Any] = {}
            raw = str(tool_input or "").strip()
            if raw:
                try:
                    parsed = json.loads(raw) if raw[:1] in "{[" else ast.literal_eval(raw)
                    if isinstance(parsed, dict):
                        args = parsed
                except Exception:
                    if tool_name == "web_search":
                        args = {"q": raw[:500]}
                    else:
                        args = {"input_preview": raw[:240]}
            safe_args = self._safe_retry_kwargs(args)
            arguments_hash = hashlib.sha256(
                json.dumps(safe_args, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
            ).hexdigest()
            context = self._state_store.get_thread_state(self._thread_key())
            binding = dict(getattr(self, "_active_research_binding", None) or {})
            tool_item = self._state_store.add_item(
                turn_id=turn_id,
                item_type="tool_run",
                status="started",
                payload={
                    "tool_name": tool_name,
                    "arguments_hash": arguments_hash,
                    "requirement_id": str(binding.get("requirement_id") or ""),
                    "attempt_id": str(binding.get("attempt_id") or ""),
                },
                session_id=self._thread_key(),
                project_id=str(context.active_project_id or ""),
                tool_run_id=rid,
            )
            self._state_store.create_tool_run(
                turn_id=turn_id,
                tool_name=tool_name,
                session_id=self._thread_key(),
                project_id=str(context.active_project_id or ""),
                run_id=rid,
                item_id=tool_item.id,
                canonical_arguments=safe_args,
                canonical_arguments_hash=arguments_hash,
                requirement_id=str(binding.get("requirement_id") or ""),
                attempt_id=str(binding.get("attempt_id") or ""),
            )
        except Exception as exc:
            logger.debug("Durable ToolRun start failed: {}", exc)

    def _dequeue_tool_run(self, run_id: str, tool_name: str = "") -> None:
        """Remove a registered stream id so it cannot be stolen by a later claim."""
        rid = str(run_id or "").strip()
        if not rid:
            return
        scope = self._tool_run_registration_scope()
        queue = self._registered_tool_runs.get(scope, [])
        for index, (name, existing_id) in enumerate(list(queue)):
            if existing_id == rid or (tool_name and name == tool_name and existing_id == rid):
                queue.pop(index)
                break
        if not queue:
            self._registered_tool_runs.pop(scope, None)

    def _tool_run_registration_scope(self) -> str:
        """Execution identity survives LangChain callback/worker thread hops."""

        return str(
            getattr(self, "_current_execution_id", "")
            or getattr(self, "_current_request_id", "")
            or self._thread_key()
        ).strip() or "default"

    def _register_tool_run(self, tool_name: str, run_id: str) -> None:
        """Bind a callback run id to its exact invocation in this Execution."""
        scope = self._tool_run_registration_scope()
        queue = self._registered_tool_runs.setdefault(scope, [])
        rid = str(run_id or "").strip()
        if not rid:
            return
        pair = (str(tool_name or "").strip(), rid)
        # Keep one slot per exact id (re-register is a no-op).
        if any(existing_id == rid for _, existing_id in queue):
            return
        queue.append(pair)

    def _claim_tool_run(self, tool_name: str, preferred_run_id: str = "") -> str:
        """Return a pre-registered ToolRun id for this exact tool.

        Prefer preferred_run_id when registered. A name-only claim is accepted
        only when exactly one unambiguous id exists in the current Execution.
        """
        scope = self._tool_run_registration_scope()
        queue = self._registered_tool_runs.get(scope, [])
        want = str(tool_name or "").strip()
        prefer = str(preferred_run_id or "").strip()
        if prefer:
            for index, (name, run_id) in enumerate(queue):
                if run_id == prefer and (not want or name == want or not name):
                    queue.pop(index)
                    if not queue:
                        self._registered_tool_runs.pop(scope, None)
                    return prefer
            # Preferred id was streamed but already claimed — keep identity, don't mint.
            return prefer
        matches = [(index, run_id) for index, (name, run_id) in enumerate(queue) if name == want]
        if len(matches) == 1:
            index, run_id = matches[0]
            queue.pop(index)
            if not queue:
                self._registered_tool_runs.pop(scope, None)
            return run_id
        if len(matches) > 1:
            raise RuntimeError(
                f"Ambiguous ToolRun identity for {want!r}: exact run_id is required"
            )
        return str(uuid.uuid4())

    def get_tool_outcome(self, run_id: str) -> Optional[ToolOutcome]:
        return self._tool_outcomes_by_run_id.get(str(run_id or ""))

    @staticmethod
    def _safe_retry_kwargs(params: Dict[str, Any]) -> Dict[str, Any]:
        safe: Dict[str, Any] = {}
        for key, value in dict(params or {}).items():
            low = str(key or "").lower()
            if any(token in low for token in ("password", "token", "secret", "api_key", "credential")):
                safe[key] = "[redacted]"
            else:
                safe[key] = value
        return safe

    def _normalize_tool_outcome(
        self,
        *,
        tool_name: str,
        output: Any = "",
        error: Optional[BaseException] = None,
        started_at: Optional[float] = None,
    ) -> ToolOutcome:
        """Convert every raw tool return into one execution-truth value."""
        now = time.time()
        name = str(tool_name or "tool").strip() or "tool"
        raw = str(output or "").strip()
        low = raw.lower()
        if error is not None:
            message = str(error)
            winerror = getattr(error, "winerror", None)
            if winerror in {5, 740}:
                code = "os_elevation_required" if winerror == 740 else "os_permission_denied"
                retryable = True
            else:
                code = "tool_exception"
                retryable = True
            return ToolOutcome(
                tool_name=name,
                execution_id=str(getattr(self, "_current_execution_id", None) or ""),
                success=False,
                status="tool_failure",
                error_code=code,
                error_message=message,
                retryable=retryable,
                started_at=started_at or now,
                completed_at=now,
            )

        # Retrieval transports report two independent axes. A completed API
        # request with no matching data is execution success, but it is not a
        # usable factual result and cannot satisfy completion gates.
        explicit_execution = re.search(r"(?i)\bexecution_status\s*=\s*([a-z_]+)", raw)
        explicit_result = re.search(r"(?i)\bresult_state\s*=\s*([a-z_]+)", raw)
        if explicit_execution and explicit_result:
            execution_state = explicit_execution.group(1).lower()
            result_state = explicit_result.group(1).lower()
            explicit_retryable = re.search(
                r"(?i)\bretryable\s*=\s*(true|false)", raw
            )
            explicit_provider = re.search(
                r"(?i)\bprovider\s*=\s*([a-z0-9_.:-]+)", raw
            )
            retryable = (
                explicit_retryable.group(1).lower() == "true"
                if explicit_retryable
                else execution_state != "success"
            )
            return ToolOutcome(
                tool_name=name,
                execution_id=str(getattr(self, "_current_execution_id", None) or ""),
                success=execution_state == "success",
                status="success" if execution_state == "success" else "tool_failure",
                execution_status=execution_state,
                result_state=result_state,
                output=raw if execution_state == "success" else "",
                error_code=(
                    ""
                    if execution_state == "success"
                    else result_state or "retrieval_failed"
                ),
                error_message="" if execution_state == "success" else raw,
                retryable=retryable,
                provider=explicit_provider.group(1) if explicit_provider else "",
                started_at=started_at or now,
                completed_at=now,
            )

        # Raw legacy tools still return strings. Recognize their structured
        # failure envelopes here so a blocked/no-op mutation can never become a
        # successful ToolRun or changed-file projection.
        if low.startswith("mutation blocked:"):
            return ToolOutcome(
                tool_name=name,
                execution_id=str(getattr(self, "_current_execution_id", None) or ""),
                success=False,
                status="validation_failure",
                error_code="mutation_precondition_failed",
                error_message=raw,
                retryable=True,
                started_at=started_at or now,
                completed_at=now,
            )
        if name == "terminal_run":
            exit_match = re.search(r"(?i)\bexitcode\s*=\s*(-?\d+)", raw)
            terminal_status = re.search(r"(?i)\bstatus\s*=\s*([a-z_]+)", raw)
            status_value = str(terminal_status.group(1) if terminal_status else "").lower()
            if (
                (exit_match and int(exit_match.group(1)) != 0)
                or status_value in {"fail", "failed", "timeout", "sandbox_unavailable", "blocked"}
                or low.startswith("command blocked by terminal denylist")
            ):
                policy_block = "blocked" in status_value or "denylist" in low or "sandbox_unavailable" in low
                return ToolOutcome(
                    tool_name=name,
                    execution_id=str(getattr(self, "_current_execution_id", None) or ""),
                    success=False,
                    status="policy_block" if policy_block else "tool_failure",
                    error_code=(
                        "terminal_sandbox_unavailable"
                        if "sandbox_unavailable" in low
                        else "terminal_timeout"
                        if status_value == "timeout" or "timed out" in low
                        else "terminal_command_failed"
                    ),
                    error_message=raw,
                    retryable=not policy_block,
                    policy_block=policy_block,
                    started_at=started_at or now,
                    completed_at=now,
                )

        policy_patterns = (
            "system actions are disabled",
            "is disabled by system configuration",
            "not allowed by thread context",
            "is not allowed by the current",
            "is outside the current thread execution context",
            "path not allowed",
            "cwd not allowed",
            "file write is disabled",
            "file operations are disabled",
            "terminal commands are disabled",
            "command rejected",
            "command blocked by terminal denylist",
            "blocked by system action permissions",
            "is blocked by echospeak",
            "approval is required before",
            "not allowlisted",
            "no applications are allowlisted",
        )
        validation_patterns = (
            "calculation error:",
            "validation error",
            "invalid argument",
            "invalid syntax",
            "missing required",
            "refusing to write content",
        )
        failure_prefixes = (
            "failed",
            "error:",
            "action failed",
            "tool failed",
            "rejected stub write",
            "file not found",
            "path is a directory",
            "binary file detected",
            "unsupported text encoding",
            "no content provided",
            "source path not found",
            "destination already exists",
            "path not found",
            "rejected unresolved planner template",
            "rejected unresolved planner template path",
            "local_filesystem — web_search blocked",
            "[local_filesystem",
        )
        policy_block = any(token in low for token in policy_patterns)
        validation_failure = any(token in low for token in validation_patterns) or (
            "unresolved planner template" in low
        )
        tool_failure = low.startswith(failure_prefixes) or any(
            low.startswith(p) for p in ("file not found", "path is a directory")
        )
        success = bool(raw) and not (policy_block or validation_failure or tool_failure)
        if not raw:
            success = False
        if policy_block:
            status = "policy_block"
            code = "configuration_or_scope_block"
            retryable = False
        elif validation_failure:
            status = "validation_failure"
            code = "invalid_tool_arguments"
            retryable = False
        elif not success:
            status = "tool_failure"
            code = "tool_returned_error"
            retryable = True
        else:
            status = "success"
            code = ""
            retryable = False
        return ToolOutcome(
            tool_name=name,
            execution_id=str(getattr(self, "_current_execution_id", None) or ""),
            success=success,
            status=status,
            output=raw if success else "",
            error_code=code,
            error_message="" if success else (raw or "The tool returned no result"),
            retryable=retryable,
            policy_block=policy_block,
            started_at=started_at or now,
            completed_at=now,
        )

    def _promote_materialized_project(self, tool_name: str, success: bool) -> None:
        """Turn an explicitly planned/materialized folder into its Project record."""
        if not success or tool_name not in {"file_mkdir", "file_write", "artifact_write", "terminal_run"}:
            return
        state = self._state_store.get_thread_state(self._thread_key())
        if state.active_project_id or not state.project_path:
            return
        try:
            root = Path(state.project_path).expanduser().resolve()
            if not root.is_dir():
                return
            from agent.projects import get_project_manager
            project = get_project_manager().attach_folder(str(root), trust_state="trusted")
            self._active_project_id = project.id
            self._execution_context = self._state_store.update_thread_state(
                self._thread_key(), active_project_id=project.id,
                project_path=str(root), workspace_root=str(root),
            )
        except Exception as exc:
            logger.debug("Could not promote materialized folder to Project: {}", exc)

    def _persist_tool_outcome(self, outcome: ToolOutcome, params: Optional[Dict[str, Any]] = None) -> ToolOutcome:
        self._promote_materialized_project(outcome.tool_name, outcome.success)
        context = self._state_store.get_thread_state(self._thread_key())
        try:
            from agent.retrieval_contracts import (
                ExecutionStatus,
                ResultState,
                RetrievalDomain,
                infer_result_state,
                infer_retrieval_domain,
            )

            query_text = str(
                (params or {}).get("q")
                or (params or {}).get("query")
                or getattr(self, "_active_user_query", "")
                or ""
            )
            result_state = infer_result_state(
                outcome.tool_name,
                outcome.output or outcome.error_message,
                success=outcome.success,
            ).value
            provider = str(outcome.provider or outcome.tool_name or "")
            confidence = outcome.confidence
            domain = infer_retrieval_domain(query_text)
            if outcome.tool_name == "web_search" and domain == RetrievalDomain.FLIGHTS:
                # General search is research evidence, never authoritative live
                # availability/status for a credentialed flight system.
                result_state = ResultState.INSUFFICIENT_EVIDENCE.value
                confidence = min(float(confidence if confidence is not None else 0.45), 0.45)
            elif outcome.tool_name == "web_search" and domain == RetrievalDomain.SOCIAL_METRIC:
                confidence = min(float(confidence if confidence is not None else 0.5), 0.5)
            outcome = outcome.model_copy(update={
                "execution_status": (
                    ExecutionStatus.SUCCESS.value if outcome.success
                    else ExecutionStatus.CANCELLED.value if str(outcome.status).casefold() in {"cancelled", "canceled", "interrupted"}
                    else ExecutionStatus.BLOCKED.value if outcome.policy_block or str(outcome.status).casefold() in {"blocked", "policy_block", "approval_required"}
                    else ExecutionStatus.ERROR.value
                ),
                "result_state": result_state,
                "provider": provider,
                "observed_at": outcome.observed_at or time.time(),
                "confidence": confidence,
            })
        except Exception as result_contract_exc:
            logger.warning("ToolOutcome result-state projection failed closed: {}", result_contract_exc)
        rid = str(outcome.run_id or "").strip()
        arguments_hash = hashlib.sha256(
            json.dumps(dict(params or {}), sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        # Trailing callback after a successful finish: keep first terminal truth.
        if rid and getattr(self, "_tool_outcomes_by_run_id", None):
            prior = self._tool_outcomes_by_run_id.get(rid)
            if prior is not None and prior.success and not outcome.success:
                logger.debug(
                    "Ignoring trailing failed outcome for already-successful ToolRun {}",
                    rid,
                )
                self._dequeue_tool_run(rid, outcome.tool_name)
                return prior
        binding = dict(getattr(self, "_active_research_binding", None) or {})
        prior_verification = dict(outcome.verification or {})
        query_plan = dict(getattr(self, "_last_research_query_plan", None) or {})
        if (
            str(query_plan.get("requirement_id") or "") != str(binding.get("requirement_id") or "")
            or str(query_plan.get("attempt_id") or "") != str(binding.get("attempt_id") or "")
        ):
            query_plan = {}
        if query_plan:
            prior_verification["query_plan"] = query_plan
            prior_verification["query_plan_id"] = str(query_plan.get("query_plan_id") or "")
        structured_fields: list[str] = []
        try:
            raw_output = str(outcome.output or "").strip()
            first_object, last_object = raw_output.find("{"), raw_output.rfind("}")
            if 0 <= first_object < last_object:
                decoded = json.loads(raw_output[first_object:last_object + 1])
                if isinstance(decoded, dict):
                    structured_fields = sorted(str(key) for key in decoded.keys())[:80]
        except (TypeError, ValueError):
            structured_fields = []
        non_information = {
            "", "queued", "started", "complete", "completed",
            "tool executed successfully.", "(search expanded)", "search expanded",
        }
        meaningful_result = bool(
            outcome.success
            and str(outcome.output or "").strip().casefold() not in non_information
            and str(outcome.result_state or "") in {
                "data_found", "verified_absence",
            }
        )
        provider_result_tools = {
            "web_search", "safe_web_fetch", "weather_live", "sports_live",
            "youtube_transcript", "calculate", "get_system_time",
            "file_read", "file_list", "project_status", "system_info",
            "email_read_inbox", "email_search", "email_get_thread",
            "discord_read_channel", "discord_web_read_recent",
        }
        active_task = getattr(self, "_active_task_run", None)
        active_requirement = next(
            (
                item for item in list(getattr(active_task, "requirements", None) or [])
                if item.requirement_id == str(binding.get("requirement_id") or outcome.requirement_id or "")
            ),
            None,
        )
        semantic_result_match = True
        semantic_covered_fields: list[str] = []
        if meaningful_result and active_requirement is not None:
            from agent.research_runtime import verify_tool_result_semantics

            semantic_verification = {
                **prior_verification,
                "covered_fields": list(dict.fromkeys([
                    *list(prior_verification.get("covered_fields") or []),
                    *structured_fields,
                ])),
            }
            semantic_result_match, semantic_covered_fields = verify_tool_result_semantics(
                str(outcome.output or ""),
                active_requirement,
                semantic_verification,
                tool_name=str(outcome.tool_name or ""),
            )
        verified_absence = str(outcome.result_state or "") == "verified_absence"
        from agent.research_runtime import verified_absence_contract_is_valid

        absence_contract_valid = bool(
            verified_absence
            and verified_absence_contract_is_valid(prior_verification)
        )
        provider_verified = bool(
            meaningful_result
            and outcome.tool_name in provider_result_tools
            and active_requirement is not None
            and semantic_result_match
            and (not verified_absence or absence_contract_valid)
        )
        terminal_verified = bool(
            outcome.tool_name == "terminal_run"
            and outcome.success
            and re.search(r"(?im)^ExitCode=0\s*$", str(outcome.output or ""))
            and re.search(r"(?im)^Status=pass\s*$", str(outcome.output or ""))
        )
        preview_verified = False
        if outcome.tool_name in {"code_preview_start", "code_preview_stop"} and outcome.success:
            try:
                preview_payload = json.loads(str(outcome.output or ""))
                preview_verified = bool(
                    isinstance(preview_payload, dict)
                    and preview_payload.get("ok") is True
                )
            except (TypeError, ValueError):
                preview_verified = False
        prior_information_verified = prior_verification.get("verified") is True
        if outcome.tool_name in provider_result_tools:
            prior_information_verified = bool(
                prior_information_verified
                and active_requirement is not None
                and semantic_result_match
                and (not verified_absence or absence_contract_valid)
            )
        information_verified = bool(
            prior_information_verified
            or provider_verified
            or terminal_verified
            or preview_verified
        )
        verification_kind = str(prior_verification.get("verification_kind") or "")
        if not verification_kind:
            verification_kind = (
                "structured_result" if structured_fields and information_verified
                else "provider_result_contract" if provider_verified
                else "terminal_exit_contract" if terminal_verified
                else "preview_state_contract" if preview_verified
                else "execution_only"
            )
        outcome = outcome.model_copy(update={
            "project_id": str(context.active_project_id or ""),
            "session_id": self._thread_key(),
            "turn_id": str(outcome.execution_id or getattr(self, "_current_execution_id", None) or ""),
            "verification": {
                **prior_verification,
                "verified": information_verified,
                "execution_verified": bool(outcome.success),
                "verifier_id": str(
                    prior_verification.get("verifier_id")
                    or "tool_result_contract_v1"
                ),
                "verification_kind": verification_kind,
                "covered_fields": list(dict.fromkeys([
                    *list(prior_verification.get("covered_fields") or []),
                    *structured_fields,
                    *semantic_covered_fields,
                ]))[:80],
                "unavailable_fields": list(
                    prior_verification.get("unavailable_fields") or []
                )[:80],
                "runtime_boundary": "EchoSpeakAgent._persist_tool_outcome",
                "arguments_hash": arguments_hash,
                "status_observed": str(outcome.status or ""),
                "verified_at": time.time(),
                "requirement_id": str(binding.get("requirement_id") or outcome.requirement_id or ""),
                "attempt_id": str(binding.get("attempt_id") or outcome.attempt_id or ""),
                "research_strategy": str(binding.get("strategy") or ""),
                "semantic_requirement_match": bool(semantic_result_match),
                "verified_absence": bool(absence_contract_valid),
            },
            "requirement_id": str(binding.get("requirement_id") or outcome.requirement_id or ""),
            "attempt_id": str(binding.get("attempt_id") or outcome.attempt_id or ""),
        })
        evidence = None
        if outcome.requirement_id and outcome.attempt_id:
            task = getattr(self, "_active_task_run", None)
            requirement = next(
                (
                    item for item in list(getattr(task, "requirements", None) or [])
                    if item.requirement_id == outcome.requirement_id
                ),
                None,
            )
            if requirement is None:
                raise RuntimeError("ToolOutcome requirement binding is stale or outside the current TaskRun")
            from agent.research_runtime import evidence_from_tool_outcome

            evidence = evidence_from_tool_outcome(
                outcome, requirement=requirement, attempt_id=outcome.attempt_id
            )
            outcome = outcome.model_copy(update={"evidence_ids": [evidence.evidence_id]})
        self._last_boundary_outcome = outcome
        if rid:
            # Only store if not already terminal-success (idempotent in-memory)
            existing_mem = (self._tool_outcomes_by_run_id or {}).get(rid)
            if existing_mem is None or not existing_mem.success or outcome.success:
                if not hasattr(self, "_tool_outcomes_by_run_id") or self._tool_outcomes_by_run_id is None:
                    self._tool_outcomes_by_run_id = {}
                self._tool_outcomes_by_run_id[rid] = outcome
        if rid:
            turn_id = str(outcome.turn_id or "")
            existing_runs = self._state_store.list_tool_runs(turn_id) if turn_id else []
            if not any(run.id == rid for run in existing_runs):
                tool_item = self._state_store.add_item(
                    turn_id=turn_id,
                    item_type="tool_run",
                    status="complete" if outcome.success else "blocked" if outcome.policy_block else "failed",
                    payload={"tool_name": outcome.tool_name, "arguments_hash": arguments_hash},
                    session_id=outcome.session_id,
                    project_id=outcome.project_id,
                    tool_run_id=rid,
                )
                self._state_store.create_tool_run(
                    turn_id=turn_id,
                    tool_name=outcome.tool_name,
                    session_id=outcome.session_id,
                    project_id=outcome.project_id,
                    run_id=rid,
                    item_id=tool_item.id,
                    canonical_arguments=dict(params or {}),
                    canonical_arguments_hash=arguments_hash,
                    action_id=outcome.action_id,
                    retry_of=str(
                        (getattr(self, "_active_retry_action", None) or {}).get("tool_run_id")
                        or ((getattr(self, "_active_approved_action", None) or {}).get("retry_state") or {}).get("tool_run_id")
                        or ""
                    ),
                    requirement_id=outcome.requirement_id,
                    attempt_id=outcome.attempt_id,
                )
            finished = self._state_store.finish_tool_run(rid, outcome)
        else:
            finished = None
        if finished is not None:
            try:
                from agent.skill_execution import record_skill_tool_outcome

                record_skill_tool_outcome(self._state_store, finished)
            except Exception as exc:
                logger.debug("SkillExecution child ToolRun linkage failed: {}", exc)
        # Research artifact handoff: durable evidence/provenance record. TaskRun
        # stores only references and requirement status, never a competing copy.
        artifact_id = ""
        try:
            if (
                finished is not None
                and str(getattr(finished, "status", "") or "").lower() in {"complete", "completed"}
                and evidence is not None
            ):
                from agent.research_artifacts import (
                    build_research_artifact_from_tool_output,
                    save_research_artifact,
                )

                out_text = ""
                try:
                    out_text = str((finished.outcome or {}).get("output") or "")
                except Exception:
                    out_text = str(getattr(outcome, "output", "") or "")
                query = ""
                try:
                    query = str((finished.canonical_arguments or {}).get("query") or (params or {}).get("q") or (params or {}).get("query") or "")
                except Exception:
                    query = ""
                art = build_research_artifact_from_tool_output(
                    output=out_text,
                    query=query,
                    project_id=str(getattr(finished, "project_id", "") or ""),
                    session_id=str(getattr(finished, "session_id", "") or self._thread_key()),
                    execution_id=str(getattr(finished, "turn_id", "") or ""),
                    tool_run_id=str(getattr(finished, "id", "") or ""),
                    objective=str(getattr(getattr(self, "_current_mode_decision", None), "objective", "") or query),
                    model_provider=str(self.llm_provider.value),
                    model_id=str(getattr(getattr(self, "_active_model_profile", None), "model_id", "") or self.provider_info.get("model") or "default"),
                    execution_status=str(outcome.execution_status or ""),
                    result_state=str(outcome.result_state or ""),
                    provider=str(outcome.provider or ""),
                    observed_at=outcome.observed_at,
                    confidence=outcome.confidence,
                    requirement_id=outcome.requirement_id,
                    attempt_id=outcome.attempt_id,
                    evidence_id=evidence.evidence_id,
                    covered_fields=list(evidence.covered_fields),
                    unavailable_fields=list(evidence.unavailable_fields),
                    verified=bool(evidence.usable),
                )
                if art.status == "ready" and art.session_id:
                    save_research_artifact(art)
                    artifact_id = art.id
                    try:
                        self._state_store.add_item(
                            turn_id=str(getattr(finished, "turn_id", "") or ""),
                            item_type="research_artifact",
                            status="complete",
                            payload={"artifact_id": art.id, "query": query, "citations": len(art.citations)},
                            session_id=str(getattr(finished, "session_id", "") or ""),
                            project_id=str(getattr(finished, "project_id", "") or ""),
                        )
                    except Exception:
                        pass
        except Exception as exc:
            logger.warning("Research artifact handoff failed: {}", exc)
        if evidence is not None:
            self._apply_bound_requirement_evidence(evidence, artifact_id)
        # Always clear registration for this exact id after terminal attempt.
        if rid:
            self._dequeue_tool_run(rid, outcome.tool_name)
        if (
            finished is not None
            and str(finished.status or "").lower() in {"complete", "completed", "success"}
            and not outcome.success
        ):
            # Durable already terminal-success; return stored success.
            try:
                return self._tool_outcomes_by_run_id.get(rid) or outcome
            except Exception:
                pass
        retry_target: Dict[str, Any] = {}

        if outcome.retryable and ToolRegistry.get(outcome.tool_name) is not None:
            active_approved = getattr(self, "_active_approved_action", None)
            approval_id = (
                str(active_approved.get("approval_id") or "")
                if isinstance(active_approved, dict) and str(active_approved.get("tool") or "") == outcome.tool_name
                else str(context.pending_approval_id or "")
            )
            retry_target = {
                "schema_version": 1,
                "lifecycle": "retryable",
                "created_at": time.time(),
                "expires_at": time.time() + (6 * 3600),
                "thread_id": self._thread_key(),
                "objective": str(context.objective or ""),
                "project_path": str(context.project_path or ""),
                "workspace_root": str(context.workspace_root or ""),
                "workspace_id": str(context.workspace_id or ""),
                "active_project_id": str(context.active_project_id or ""),
                "tool": outcome.tool_name,
                "kwargs": self._safe_retry_kwargs(params or {}),
                "arguments_hash": arguments_hash,
                "tool_run_id": outcome.run_id,
                "failure_reason": outcome.error_message,
                "error_code": outcome.error_code,
                "failure_status": outcome.status,
                "retryable": bool(outcome.retryable),
                "approval_id": approval_id,
                "action_id": str(outcome.action_id or ""),
                # Failed modifying actions require a fresh confirmation because
                # the runtime cannot assume whether a partial side effect occurred.
                "approval_valid": bool(approval_id and not self._is_action_tool(outcome.tool_name)),
                "retry_count": int((context.retry_target or {}).get("retry_count") or 0),
                "execution_id": outcome.execution_id,
                "partial_side_effect_possible": bool(self._is_action_tool(outcome.tool_name)),
                "requires_regeneration": bool(outcome.error_code == "corrupted_write_content"),
            }
        self._update_thread_progress_preserving_turn_authority(
            last_tool_outcome=outcome.model_dump(),
            retry_target=retry_target,
            execution_status=(
                context.execution_status
                if outcome.status == "approval_required"
                else "in_progress" if outcome.success
                else "blocked" if outcome.policy_block
                else "retryable" if outcome.retryable
                else "failed"
            ),
            safest_next_action=(
                "Continue with the remaining plan"
                if outcome.success
                else "Resolve the authority, permission, or Project scope block before retrying"
                if outcome.policy_block
                else "Retry the same action after resolving the reported block"
                if outcome.retryable
                else "Correct the tool arguments before continuing"
            ),
        )
        return outcome

    def _invoke_authorized_raw_tool(self, raw_tool: Any, params: Optional[Dict[str, Any]] = None) -> ToolOutcome:
        """The single execution-time boundary used by every exposed tool object."""
        started_at = time.time()
        name = str(getattr(raw_tool, "name", "") or "").strip()
        run_id = self._claim_tool_run(name)
        active_action = getattr(self, "_active_approved_action", None)
        action_id = str(active_action.get("action_id") or "") if isinstance(active_action, dict) else ""
        arguments = dict(params or {})
        if name == "web_search" and "query" in arguments and "q" not in arguments:
            arguments["q"] = arguments.pop("query")
        if name == "calculate" and "expr" in arguments and "expression" not in arguments:
            arguments["expression"] = arguments.pop("expr")
        approval_arguments = dict(arguments)

        if not name or ToolRegistry.get(name) is None:
            outcome = ToolOutcome(
                tool_name=name or "unknown",
                execution_id=str(getattr(self, "_current_execution_id", None) or ""),
                success=False,
                status="policy_block",
                error_code="unregistered_tool",
                error_message=f"Tool '{name or 'unknown'}' is not registered.",
                policy_block=True,
                started_at=started_at,
                completed_at=time.time(),
            )
            return self._persist_tool_outcome(outcome.model_copy(update={"run_id": run_id, "action_id": action_id}), arguments)

        current_state = self._state_store.get_thread_state(self._thread_key())
        if not ToolRegistry.available_in_scope(
            name,
            project_id=str(current_state.active_project_id or ""),
            session_id=self._thread_key(),
        ):
            entry = ToolRegistry.get(name)
            outcome = ToolOutcome(
                tool_name=name,
                execution_id=str(getattr(self, "_current_execution_id", None) or ""),
                success=False,
                status="policy_block",
                error_code="tool_unavailable",
                error_message=(
                    f"Tool '{name}' is unavailable in the current Project/Session scope"
                    + (f": {entry.unavailable_reason}" if entry and entry.unavailable_reason else ".")
                ),
                policy_block=True,
                started_at=started_at,
                completed_at=time.time(),
            )
            return self._persist_tool_outcome(
                outcome.model_copy(update={"run_id": run_id, "action_id": action_id}), arguments
            )

        schema = getattr(raw_tool, "args_schema", None) or getattr(ToolRegistry.get(name).func, "args_schema", None)
        if schema is not None:
            try:
                validated = schema.model_validate(arguments)
                arguments = validated.model_dump(exclude_none=True)
            except Exception as exc:
                outcome = ToolOutcome(
                    tool_name=name,
                    execution_id=str(getattr(self, "_current_execution_id", None) or ""),
                    success=False,
                    status="validation_failure",
                    error_code="invalid_tool_arguments",
                    error_message=f"Invalid arguments for {name}: {exc}",
                    retryable=False,
                    started_at=started_at,
                    completed_at=time.time(),
                )
                return self._persist_tool_outcome(outcome.model_copy(update={"run_id": run_id, "action_id": action_id}), arguments)
        if isinstance(raw_tool, Tool) and name == "web_search" and "query" in arguments and "q" not in arguments:
            arguments["q"] = arguments.pop("query")
        if name == "calculate" and not _is_valid_math_expression(str(arguments.get("expression") or "")):
            outcome = ToolOutcome(
                tool_name=name,
                execution_id=str(getattr(self, "_current_execution_id", None) or ""),
                success=False,
                status="validation_failure",
                error_code="invalid_math_expression",
                error_message="The calculator accepts mathematical expressions only.",
                retryable=False,
                started_at=started_at,
                completed_at=time.time(),
            )
            return self._persist_tool_outcome(outcome.model_copy(update={"run_id": run_id, "action_id": action_id}), arguments)

        # Corrupted-file safety: never write unresolved SEARCH/REPLACE markers to disk.
        if name == "file_write":
            write_body = str(
                arguments.get("content")
                or arguments.get("text")
                or arguments.get("body")
                or ""
            )
            if self._content_has_unresolved_edit_markers(write_body):
                outcome = ToolOutcome(
                    tool_name=name,
                    execution_id=str(getattr(self, "_current_execution_id", None) or ""),
                    success=False,
                    status="validation_failure",
                    error_code="corrupted_write_content",
                    error_message=(
                        "Refusing to write content that still contains unresolved "
                        "SEARCH/REPLACE or conflict markers. Fix the edit blocks first."
                    ),
                    retryable=True,
                    started_at=started_at,
                    completed_at=time.time(),
                )
                return self._persist_tool_outcome(
                    outcome.model_copy(update={"run_id": run_id, "action_id": action_id}),
                    arguments,
                )

        approved = self._approved_action_matches(name, approval_arguments) or self._approved_action_matches(name, arguments)
        if not self._tool_allowed(name) and not approved:
            outcome = ToolOutcome(
                tool_name=name,
                execution_id=str(getattr(self, "_current_execution_id", None) or ""),
                success=False,
                status="policy_block",
                error_code="tool_scope_or_policy_block",
                error_message=(
                    f"Tool '{name}' is not allowed by the current Session scope, "
                    "role policy, or turn tool inventory."
                ),
                retryable=False,
                policy_block=True,
                started_at=started_at,
                completed_at=time.time(),
            )
            return self._persist_tool_outcome(outcome.model_copy(update={"run_id": run_id, "action_id": action_id}), arguments)

        if self._is_action_tool(name):
            approved_action = getattr(self, "_active_approved_action", None) if approved else None
            # Hard gate: web/UI mutations require an explicit active approval for this call.
            src = str(getattr(self, "_current_source", "") or "").strip().lower()
            if (
                name in {"file_write", "file_delete", "file_move", "file_copy", "file_mkdir", "artifact_write"}
                and (not src or src == "web")
                and not approved
                and not (
                    isinstance(approved_action, dict)
                    and str(approved_action.get("tool") or "") == name
                    and bool(approved_action.get("_decision_authorized"))
                )
            ):
                outcome = ToolOutcome(
                    tool_name=name,
                    execution_id=str(getattr(self, "_current_execution_id", None) or ""),
                    success=False,
                    status="approval_required",
                    error_code="approval_required",
                    error_message=(
                        f"Approval is required before {name} can run. "
                        "Propose the change and wait for explicit user confirmation."
                    ),
                    retryable=True,
                    policy_block=False,
                    started_at=started_at,
                    completed_at=time.time(),
                )
                return self._persist_tool_outcome(
                    outcome.model_copy(update={"run_id": run_id, "action_id": action_id}),
                    arguments,
                )
            if not self._action_allowed(name, approval_arguments, approved_action):
                outcome = ToolOutcome(
                    tool_name=name,
                    execution_id=str(getattr(self, "_current_execution_id", None) or ""),
                    success=False,
                    status="policy_block",
                    error_code="action_configuration_or_constraint_block",
                    error_message=(
                        f"Action '{name}' is blocked by configuration, permission flags, "
                        "role, Project constraints, or user constraints — not by chat/research/coding mode."
                    ),
                    retryable=False,
                    policy_block=True,
                    started_at=started_at,
                    completed_at=time.time(),
                )
                return self._persist_tool_outcome(outcome.model_copy(update={"run_id": run_id, "action_id": action_id}), arguments)
            if not approved and not self._should_auto_confirm(name):
                existing = self._state_store.get_pending_approval(self._thread_key())
                if existing is None or existing.tool != name or dict(existing.kwargs or {}) != arguments:
                    pending = {
                        "tool": name,
                        "kwargs": arguments,
                        "original_input": str(getattr(self, "_active_user_query", None) or self._execution_context.objective or ""),
                    }
                    self._set_pending_action(
                        pending,
                        f"Run {name} with the validated arguments shown in this approval",
                        pending["original_input"],
                    )
                outcome = ToolOutcome(
                    tool_name=name,
                    execution_id=str(getattr(self, "_current_execution_id", None) or ""),
                    success=False,
                    status="approval_required",
                    error_code="approval_required",
                    error_message=f"Approval is required before {name} can run.",
                    retryable=False,
                    policy_block=False,
                    started_at=started_at,
                    completed_at=time.time(),
                )
                return self._persist_tool_outcome(
                    outcome.model_copy(update={"run_id": run_id, "action_id": str((self._pending_action or {}).get("action_id") or "")}),
                    arguments,
                )

        cacheable_read = name in {"file_read", "file_list", "project_status"}
        cache_key = ""
        cached_read: Optional[Dict[str, Any]] = None
        if not isinstance(getattr(self, "_request_read_cache", None), dict):
            self._request_read_cache = {}
        if cacheable_read:
            cache_key = json.dumps(
                {
                    "tool": name,
                    "arguments": arguments,
                    "project": str(self._execution_context.active_project_id or ""),
                    "root": str(self._execution_context.workspace_root or ""),
                    "generation": int(getattr(self, "_request_mutation_generation", 0) or 0),
                },
                sort_keys=True, separators=(",", ":"), default=str,
            )
            cached_read = dict(self._request_read_cache.get(cache_key) or {}) or None

        mutation_precondition: Dict[str, Any] = {}
        try:
            from agent.tools import update_tool_execution_context

            update_tool_execution_context(
                approval_id=(
                    str(active_action.get("approval_id") or "")
                    if isinstance(active_action, dict)
                    else ""
                ),
                tool_run_id=run_id,
                task_run_id=str(getattr(getattr(self, "_active_task_run", None), "id", "") or ""),
                requirement_id=str(
                    dict(getattr(self, "_active_research_binding", None) or {}).get("requirement_id") or ""
                ),
                attempt_id=str(
                    dict(getattr(self, "_active_research_binding", None) or {}).get("attempt_id") or ""
                ),
            )
        except Exception:
            pass
        if name in {
            "file_write", "file_delete", "file_move", "file_copy", "file_mkdir",
            "artifact_write", "notepad_write", "checkpoint_undo",
        }:
            approval_id = str(active_action.get("approval_id") or "") if isinstance(active_action, dict) else ""
            approval_record = self._state_store.get_approval(approval_id) if approval_id else None
            mutation_precondition = dict(getattr(approval_record, "source_precondition", None) or {})
            try:
                from agent.tools import update_tool_execution_context
                update_tool_execution_context(mutation_precondition=mutation_precondition)
            except Exception:
                mutation_precondition = {}

        try:
            if cached_read is not None:
                result = cached_read.get("output", "")
            elif isinstance(raw_tool, Tool):
                result = raw_tool.func(**arguments)
            elif hasattr(raw_tool, "invoke"):
                try:
                    result = raw_tool.invoke(arguments)
                except TypeError:
                    result = raw_tool.invoke(**arguments)
            else:
                result = raw_tool(**arguments)
            outcome = self._normalize_tool_outcome(
                tool_name=name,
                output=result,
                started_at=started_at,
            )
            if cached_read is not None and outcome.success:
                outcome = outcome.model_copy(update={
                    "verification": {
                        "cache_hit": True,
                        "source_tool_run_id": str(cached_read.get("run_id") or ""),
                        "mutation_generation": int(getattr(self, "_request_mutation_generation", 0) or 0),
                    }
                })
            if name == "checkpoint_undo" and outcome.success:
                outcome = outcome.model_copy(update={"verification": {"checkpoint_restored": True}})
            elif name == "file_write" and outcome.success:
                try:
                    from agent.tools import strip_echo_file_wrapper
                    expected_body = strip_echo_file_wrapper(str(arguments.get("content") or ""))
                    reported_body = strip_echo_file_wrapper(str(outcome.output or ""))
                    outcome = outcome.model_copy(update={"verification": {
                        **dict(outcome.verification or {}),
                        "write_reported": True,
                        "content_length": len(expected_body),
                        "reported_content_matches": reported_body == expected_body,
                        "append": bool(arguments.get("append", False)),
                    }})
                except Exception:
                    pass
            elif name == "file_read" and outcome.success:
                outcome = outcome.model_copy(update={"verification": {
                    **dict(outcome.verification or {}),
                    "source_read": True,
                    "path": str(arguments.get("path") or ""),
                    "truncated": "(truncated)" in str(outcome.output or ""),
                }})
            elif name in {"file_delete", "file_move", "file_copy", "file_mkdir"} and outcome.success:
                from agent.tools import _mutation_path_version, _safe_file_path

                verified = False
                diagnostics: Dict[str, Any] = {}
                if name == "file_delete":
                    target = _safe_file_path(str(arguments.get("path") or ""))
                    verified = bool(target is not None and not target.exists())
                    diagnostics = {"path": str(target or ""), "absent": verified}
                elif name == "file_mkdir":
                    target = _safe_file_path(str(arguments.get("path") or ""))
                    verified = bool(target is not None and target.exists() and target.is_dir())
                    diagnostics = {"path": str(target or ""), "directory_exists": verified}
                else:
                    source = _safe_file_path(str(arguments.get("src") or ""))
                    destination = _safe_file_path(str(arguments.get("dst") or ""))
                    destination_exists = bool(destination is not None and destination.exists())
                    if name == "file_move":
                        verified = bool(source is not None and not source.exists() and destination_exists)
                    else:
                        if source is not None and source.exists() and destination_exists:
                            source_version = _mutation_path_version(source, "src")
                            destination_version = _mutation_path_version(destination, "dst")
                            verified = all(
                                source_version.get(key) == destination_version.get(key)
                                for key in ("kind", "sha256", "size")
                            )
                    diagnostics = {
                        "src": str(source or ""),
                        "dst": str(destination or ""),
                        "destination_exists": destination_exists,
                    }
                requirement = next(
                    (
                        item for item in list(getattr(getattr(self, "_active_task_run", None), "requirements", None) or [])
                        if item.requirement_id == str((getattr(self, "_active_research_binding", None) or {}).get("requirement_id") or "")
                    ),
                    None,
                )
                outcome = outcome.model_copy(update={"verification": {
                    **dict(outcome.verification or {}),
                    **diagnostics,
                    "verified": verified,
                    "execution_verified": True,
                    "verifier_id": "filesystem_postcondition_v1",
                    "verification_kind": "filesystem_postcondition",
                    "covered_fields": list(getattr(requirement, "requested_fields", None) or []),
                }})
            elif name == "terminal_run" and outcome.success:
                exit_match = re.search(r"(?i)exitcode\s*=\s*(-?\d+)", str(outcome.output or ""))
                outcome = outcome.model_copy(update={"verification": {
                    **dict(outcome.verification or {}),
                    "exit_code": int(exit_match.group(1)) if exit_match else None,
                    "command_completed": bool(exit_match and int(exit_match.group(1)) == 0),
                }})
        except Exception as exc:
            outcome = self._normalize_tool_outcome(
                tool_name=name,
                error=exc,
                started_at=started_at,
            )
        finally:
            try:
                from agent.tools import update_tool_execution_context
                update_tool_execution_context(
                    mutation_precondition={},
                    tool_run_id="",
                    task_run_id="",
                    requirement_id="",
                    attempt_id="",
                )
            except Exception:
                pass
        outcome = self._persist_tool_outcome(
            outcome.model_copy(update={"run_id": run_id, "action_id": action_id}),
            arguments,
        )
        if cacheable_read and cache_key and outcome.success and cached_read is None:
            self._request_read_cache[cache_key] = {"output": outcome.output, "run_id": outcome.run_id}
        if self._is_action_tool(name) and outcome.success:
            self._request_mutation_generation = int(getattr(self, "_request_mutation_generation", 0) or 0) + 1
            self._request_read_cache.clear()
        if outcome.status != "approval_required":
            self._boundary_record_in_progress = True
            try:
                recorded_text = outcome.output if outcome.success else outcome.error_message
                self._record_tool_execution_outcome(
                    tool_name=name,
                    tool_input=str(arguments),
                    output=recorded_text,
                    success=outcome.success,
                )
                self._last_boundary_record = (
                    name,
                    str(recorded_text or ""),
                    str(outcome.execution_id or ""),
                )
            finally:
                self._boundary_record_in_progress = False
        logger.info(
            "[ToolOutcome] thread={} execution={} tool={} status={} retryable={}",
            self._thread_key(),
            outcome.execution_id,
            name,
            outcome.status,
            outcome.retryable,
        )
        return outcome

    def _record_tool_execution_outcome(
        self,
        *,
        tool_name: str,
        tool_input: str,
        output: str,
        success: bool,
        run_id: str = "",
    ) -> bool:
        """Persist a compact factual action record; never raw tool output or file contents."""
        tool = str(tool_name or "tool")
        raw = str(output or "")
        boundary_record = getattr(self, "_last_boundary_record", None)
        boundary_in_progress = bool(getattr(self, "_boundary_record_in_progress", False))
        if (
            not boundary_in_progress
            and boundary_record is not None
            and boundary_record == (tool, raw, str(getattr(self, "_current_execution_id", None) or ""))
        ):
            self._last_boundary_record = None
            boundary_outcome = getattr(self, "_last_boundary_outcome", None)
            return bool(getattr(boundary_outcome, "success", success))
        low = raw.lower().strip()
        existing_boundary_outcome = getattr(self, "_last_boundary_outcome", None)
        outcome = (
            existing_boundary_outcome
            if boundary_in_progress and str(getattr(existing_boundary_outcome, "tool_name", "") or "") == tool
            else self._normalize_tool_outcome(tool_name=tool, output=raw)
        )
        if run_id and str(outcome.run_id or "") != str(run_id):
            outcome = outcome.model_copy(update={"run_id": str(run_id)})
        success = bool(success and outcome.success)
        if not success and outcome.success:
            outcome = outcome.model_copy(
                update={
                    "success": False,
                    "status": "tool_failure",
                    "output": "",
                    "error_code": "tool_reported_failure",
                    "error_message": raw or "Tool execution failed",
                    "retryable": True,
                }
            )
        try:
            summary = self._sanitize_tool_preview(tool, raw)
        except Exception:
            summary = re.sub(r"\s+", " ", raw.splitlines()[0] if raw else "")[:240]
        if tool == "web_search":
            query = re.sub(r"\s+", " ", str(tool_input or "")).strip()[:140]
            accepted = "accepted" if "accepted=true" in low else "limited"
            count_match = re.search(r"evidence_count\s*[=:]\s*(\d+)", low)
            evidence_count = count_match.group(1) if count_match else "unknown"
            summary = f"Research {accepted} for {query or 'the active objective'}; evidence_count={evidence_count}"
        summary = re.sub(r"(?i)(api[_ -]?key|password|token|secret)\s*[:=]\s*\S+", r"\1=[redacted]", summary)[:240]
        verified = bool(
            success
            and (
                tool == "project_status"
                or tool == "checkpoint_undo"
                or (tool == "terminal_run" and re.search(r"(?i)exitcode\s*=\s*0|status\s*=\s*(?:ok|success)", raw))
                or bool((outcome.verification or {}).get("reported_content_matches"))
                or bool((outcome.verification or {}).get("source_read"))
            )
        )
        action = {
            "tool": tool,
            "summary": summary or ("completed" if success else "failed"),
            "status": "complete" if success else "failed",
            "success": bool(success),
            "verified": verified,
            "execution_id": str(self._current_execution_id or ""),
        }
        if tool in {"file_write", "artifact_write", "notepad_write"}:
            provenance_input = "write arguments omitted"
        elif tool in {"file_read", "file_list", "file_move", "file_copy", "file_delete", "file_mkdir"}:
            provenance_input = re.sub(r"\s+", " ", str(tool_input or ""))[:160]
        elif tool == "terminal_run":
            command = re.sub(r"\s+", " ", str(tool_input or "")).strip()
            provenance_input = f"command={command.split(maxsplit=1)[0] if command else '(empty)'}"
        else:
            provenance_input = re.sub(r"\s+", " ", str(tool_input or ""))[:160]
        provenance_input = re.sub(
            r"(?i)(api[_ -]?key|password|token|secret)\s*[:=]\s*\S+",
            r"\1=[redacted]",
            provenance_input,
        )
        context = self._state_store.get_thread_state(self._thread_key())
        detail_args: Dict[str, Any] = {}
        active_approved = getattr(self, "_active_approved_action", None)
        if isinstance(active_approved, dict) and str(active_approved.get("tool") or "") == tool:
            detail_args = dict(active_approved.get("kwargs") or {})
        else:
            try:
                parsed_detail = ast.literal_eval(str(tool_input or ""))
                if isinstance(parsed_detail, dict):
                    detail_args = parsed_detail
            except (SyntaxError, ValueError, TypeError):
                pass
        details = dict(context.operation_details or {})
        details["tools_used"] = list(dict.fromkeys([*(details.get("tools_used") or []), tool]))[-32:]
        path_value = str(detail_args.get("path") or detail_args.get("src") or "").strip()
        if path_value and tool in {"file_read", "file_list"}:
            details["files_inspected"] = list(dict.fromkeys([*(details.get("files_inspected") or []), path_value]))[-64:]
        if success and path_value and tool in {"file_write", "file_move", "file_copy", "file_delete", "file_mkdir", "artifact_write"}:
            details["files_changed"] = list(dict.fromkeys([*(details.get("files_changed") or []), path_value]))[-64:]
        if tool == "terminal_run":
            command = re.sub(
                r"(?i)(api[_ -]?key|password|token|secret)\s*[:=]\s*\S+",
                r"\1=[redacted]",
                str(detail_args.get("command") or tool_input or ""),
            )[:240]
            if command:
                details["commands"] = [*(details.get("commands") or []), command][-24:]
        if verified:
            details["verification"] = {"status": "passed", "summary": action["summary"]}
        elif not success:
            details["unresolved"] = [*(details.get("unresolved") or []), action["summary"]][-24:]
        completed = list(context.completed_actions or [])
        failed = list(context.failed_actions or [])
        pending = [item for item in (context.pending_actions or []) if str(item.get("tool") or "") != tool]
        (completed if success else failed).append(action)
        self._update_thread_progress_preserving_turn_authority(
            completed_actions=completed,
            failed_actions=failed,
            pending_actions=pending,
            operation_details=details,
            execution_status=(
                "in_progress" if success
                else "blocked" if outcome.policy_block
                else "partially_complete" if completed
                else "retryable" if outcome.retryable
                else "failed"
            ),
            safest_next_action=(
                "Continue with the remaining plan" if success
                else f"Change the authority, permission, or Project scope blocking {tool}"
                if outcome.policy_block
                else f"Retry {tool} after resolving the reported failure"
                if outcome.retryable
                else f"Resolve the {tool} failure before continuing"
            ),
        )
        self._record_ledger_entry(
            category="tool_action",
            summary=action["summary"],
            tool=tool,
            workflow=str(getattr(getattr(self, "_current_mode_profile", None), "executor_name", "") or "tool"),
            status=action["status"],
            success=bool(success),
            verified=verified,
            provenance={"input_summary": provenance_input},
            unresolved="" if success else action["summary"],
        )
        try:
            outcome_params: Dict[str, Any] = {}
            active_approved = getattr(self, "_active_approved_action", None)
            if isinstance(active_approved, dict) and str(active_approved.get("tool") or "") == tool:
                outcome_params = dict(active_approved.get("kwargs") or {})
            else:
                try:
                    parsed_input = ast.literal_eval(str(tool_input or ""))
                    if isinstance(parsed_input, dict):
                        outcome_params = parsed_input
                except (SyntaxError, ValueError, TypeError):
                    if tool == "web_search":
                        outcome_params = {"q": str(tool_input or "")}
                    elif tool in {"file_read", "file_list", "file_delete", "file_mkdir"}:
                        outcome_params = {"path": str(tool_input or "")}
            if not boundary_in_progress:
                self._persist_tool_outcome(outcome, outcome_params)
        except Exception as exc:
            logger.debug("Structured tool outcome persistence failed: {}", exc)
        return bool(success)

    def _emit_tool_end(
        self,
        callbacks: Optional[list],
        output: str,
        run_id: str,
        *,
        notify_callbacks: bool = True,
    ) -> None:
        # Record observability metrics
        rid = str(run_id or "").strip()
        tool_name = self._partial_tool_names.pop(rid, "unknown") if rid else "unknown"
        tool_input = ""
        if hasattr(self, "_partial_tool_inputs") and rid:
            tool_input = self._partial_tool_inputs.pop(rid, "")
        latency_ms = 0.0
        if hasattr(self, '_tool_start_times') and rid in self._tool_start_times:
            latency_ms = (time.time() - self._tool_start_times.pop(rid)) * 1000
        # Capture the governed result for canonical evidence projection.
        outcome_success = True
        try:
            outcome_success = self._record_tool_execution_outcome(
                tool_name=tool_name,
                tool_input=tool_input,
                output=str(output or ""),
                success=True,
                run_id=rid,
            )
        except Exception as exc:
            logger.debug("Tool ledger recording failed: {}", exc)
        # Durable finish for stream-only tools (grounded search, etc.).
        # Idempotent: if already terminal, do not re-finish or demote success.
        try:
            prior = (self._tool_outcomes_by_run_id or {}).get(rid) if rid else None
            if rid and prior is None:
                outcome = self._normalize_tool_outcome(
                    tool_name=tool_name,
                    output=str(output or ""),
                    started_at=time.time() - (latency_ms / 1000.0 if latency_ms else 0),
                )
                outcome = outcome.model_copy(update={
                    "run_id": rid,
                    "execution_id": str(getattr(self, "_current_execution_id", "") or ""),
                })
                params: Dict[str, Any] = {}
                if tool_name == "web_search":
                    params = {"q": str(tool_input or "")[:500]}
                self._persist_tool_outcome(outcome, params)
                outcome_success = bool(outcome.success)
            elif prior is not None:
                outcome_success = bool(prior.success)
                self._dequeue_tool_run(rid, tool_name)
        except Exception as exc:
            logger.debug("Stream ToolRun durable finish failed: {}", exc)
        self._partial_tool_results.append({
            "tool": tool_name,
            "output": str(output)[:4000],
            "success": bool(outcome_success),
            "execution_status": str(
                getattr((self._tool_outcomes_by_run_id or {}).get(rid), "execution_status", "") or ""
            ),
            "result_state": str(
                getattr((self._tool_outcomes_by_run_id or {}).get(rid), "result_state", "") or ""
            ),
            "run_id": rid,
        })
        try:
            from agent.observability import get_observability_collector
            get_observability_collector().record_tool_call(
                tool_name,
                latency_ms,
                success=bool(outcome_success),
                error="" if outcome_success else str(output)[:240],
            )
        except Exception:
            pass

        # Stream event
        if hasattr(self, '_stream_buffer') and self._stream_buffer:
            try:
                self._stream_buffer.push_tool_end(tool_name, str(output)[:500], rid)
            except Exception:
                pass

        if not callbacks:
            return
        if notify_callbacks:
            for cb in callbacks:
                fn = getattr(cb, "on_tool_end", None)
                if callable(fn):
                    try:
                        fn(output, rid)
                    except Exception:
                        pass
        else:
            for cb in callbacks:
                q = getattr(cb, "_q", None)
                if q is None:
                    continue
                try:
                    out = str(output or "")
                    max_len = 8000 if tool_name == "web_search" else 800
                    if len(out) > max_len:
                        out = out[:max_len] + "…"
                    event = {
                        "type": "tool_end",
                        "id": rid,
                        "name": tool_name,
                        "output": out,
                        "at": time.time(),
                        "request_id": str(getattr(self, "_current_request_id", "") or getattr(cb, "_request_id", "") or ""),
                    }
                    outcome = self.get_tool_outcome(rid)
                    if outcome is not None:
                        event["outcome"] = outcome.model_dump()
                    q.put(event)
                except Exception:
                    pass

    def _emit_tool_error(
        self,
        callbacks: Optional[list],
        error: BaseException,
        run_id: str,
        *,
        notify_callbacks: bool = True,
    ) -> None:
        # Record observability error
        rid = str(run_id or "").strip()
        tool_name = self._partial_tool_names.pop(rid, "unknown") if rid else "unknown"
        tool_input = ""
        if hasattr(self, "_partial_tool_inputs") and rid:
            tool_input = self._partial_tool_inputs.pop(rid, "")
        latency_ms = 0.0
        if hasattr(self, '_tool_start_times') and rid in self._tool_start_times:
            latency_ms = (time.time() - self._tool_start_times.pop(rid)) * 1000
        try:
            from agent.observability import get_observability_collector
            get_observability_collector().record_tool_call(tool_name, latency_ms, success=False, error=str(error))
        except Exception:
            pass
        try:
            self._record_tool_execution_outcome(
                tool_name=tool_name,
                tool_input=tool_input,
                output=str(error),
                success=False,
                run_id=rid,
            )
        except Exception as exc:
            logger.debug("Tool failure ledger recording failed: {}", exc)
        try:
            prior = (self._tool_outcomes_by_run_id or {}).get(rid) if rid else None
            if rid and prior is None:
                outcome = self._normalize_tool_outcome(
                    tool_name=tool_name,
                    error=error,
                    started_at=time.time() - (latency_ms / 1000.0 if latency_ms else 0),
                )
                outcome = outcome.model_copy(update={
                    "run_id": rid,
                    "execution_id": str(getattr(self, "_current_execution_id", "") or ""),
                })
                self._persist_tool_outcome(outcome, {"input_preview": str(tool_input or "")[:240]})
            elif prior is not None and prior.success:
                # Trailing error after success — ignore (do not demote).
                self._dequeue_tool_run(rid, tool_name)
            elif prior is not None:
                self._dequeue_tool_run(rid, tool_name)
        except Exception as exc:
            logger.debug("Stream ToolRun durable error finish failed: {}", exc)

        # Stream event
        if hasattr(self, '_stream_buffer') and self._stream_buffer:
            try:
                self._stream_buffer.push_tool_error(tool_name, str(error))
            except Exception:
                pass

        if not callbacks:
            return
        if notify_callbacks:
            for cb in callbacks:
                fn = getattr(cb, "on_tool_error", None)
                if callable(fn):
                    try:
                        fn(error, rid)
                    except Exception:
                        pass
        else:
            for cb in callbacks:
                q = getattr(cb, "_q", None)
                if q is None:
                    continue
                try:
                    q.put({
                        "type": "tool_error",
                        "id": rid,
                        "name": tool_name,
                        "error": str(error),
                        "at": time.time(),
                        "request_id": str(getattr(self, "_current_request_id", "") or getattr(cb, "_request_id", "") or ""),
                    })
                except Exception:
                    pass

    def _push_stream_event(self, event: dict) -> None:
        """Push a custom event dict to the streaming queue (reaching the frontend via /query/stream)."""
        callbacks = getattr(self, "_current_callbacks", None)
        if not callbacks:
            return
        for cb in callbacks:
            put = getattr(cb, "_put", None)
            if callable(put):
                try:
                    put(event)
                    continue
                except Exception:
                    pass
            q = getattr(cb, "_q", None)
            if q is not None:
                try:
                    q.put(event)
                except Exception:
                    pass

    def _emit_active_task_activity(self, task: Any = None) -> None:
        """Emit the bounded semantic snapshot for the current durable TaskRun."""

        active = task or getattr(self, "_active_task_run", None)
        if active is None:
            return
        try:
            from agent.stream_events import build_task_activity_event

            self._push_stream_event(build_task_activity_event(active))
        except Exception as exc:
            logger.debug("Task activity projection failed closed: {}", exc)

    def _user_has_social_open(self, user_input: str) -> bool:
        """True if the user greets or asks how Echo is (social first beat)."""
        low = re.sub(r"\s+", " ", str(user_input or "").strip().lower())
        # Normalize curly apostrophes so how're / how's still match.
        low = low.replace("\u2019", "'").replace("\u2018", "'")
        if not low:
            return False
        social = [
            r"\bhow(?:'re| are) you(?:\s+feeling)?\b",
            r"\bhow(?:'s| is) it going\b",
            r"\bhow you doing\b",
            r"\bhow(?:'re| are) things\b",
            r"\bhow(?:'s| is) everything\b",
            r"\bwhat(?:'s| is) up\b",
            r"\bwyd\b",
            r"\bhow(?:'s| is) your day\b",
            r"\b(hey|hi|hello|yo|sup)\b",
            r"\bgood (morning|afternoon|evening|night)\b",
        ]
        return any(re.search(p, low) for p in social)

    def _social_task_preamble_fallback(self, task_hint: str = "that") -> str:
        """Deterministic social-first line when the LLM preamble fails."""
        import random
        task = re.sub(r"\s+", " ", str(task_hint or "that")).strip() or "that"
        options = [
            f"Doing good — checking {task} now.",
            f"I'm good — looking into {task}.",
            f"Feeling solid — pulling {task} up.",
            f"All good here — one sec on {task}.",
            f"Pretty good — let me check {task}.",
        ]
        return random.choice(options)

    # _split_multi_intent_web_queries was removed: it was dead code after
    # Stage 3 simplification (only fired on weather+schedule combos).

    def _extract_social_handle(self, user_input: str) -> str:
        text = user_input or ""
        match = re.search(r"(?<![A-Za-z0-9])@([A-Za-z0-9_\.]{2,})", text)
        if not match:
            return ""
        return match.group(1) or ""

    def _creator_search_queries(self, user_input: str) -> list[str]:
        text = (user_input or "").strip()
        lower = text.lower()
        handle = self._extract_social_handle(text)
        if not handle:
            return []
        trigger_terms = (
            "youtube",
            "watching",
            "video",
            "channel",
            "creator",
            "stream",
            "who is",
            "who's",
            "tell me about",
            "do you know",
            "what do you know",
            "info on",
        )
        if not any(term in lower for term in trigger_terms):
            return []
        base = handle.lstrip("@").strip()
        if not base:
            return []
        return [f"{base} youtube channel", f"{base} youtube creator", f"{base} creator"]

    # ── Pipeline stage methods for process_query ──────────────────────────
    # These decompose the monolithic process_query into focused stages.
    # Each stage returns Optional[tuple[str, bool]] — tuple means "done,
    # return this", None means "continue to next stage".

    def _resolve_weather_city_hint(self, text: str = "") -> str:
        """Best-effort home/city for bare weather asks (subject, profile, config)."""
        from agent.research import _infer_city_from_text

        blobs: list[str] = [
            str(text or ""),
            str(getattr(self, "_current_subject_text", "") or ""),
            str(getattr(self, "_last_web_query_context", "") or ""),
            str(getattr(self, "_active_user_query", "") or ""),
            str(getattr(config, "default_location", "") or ""),
        ]
        # Profile facts: location / city / home_city / hometown
        try:
            mem = getattr(self, "memory", None)
            profile = getattr(mem, "_profile", None) if mem is not None else None
            if isinstance(profile, dict):
                for key in ("location", "city", "home_city", "hometown", "home_town"):
                    val = profile.get(key)
                    if val:
                        blobs.append(str(val))
                prefs = profile.get("preferences")
                if isinstance(prefs, dict):
                    for key in ("location", "city", "home_city"):
                        if prefs.get(key):
                            blobs.append(str(prefs.get(key)))
        except Exception:
            pass
        # Recent tool evidence may name a city
        try:
            for tr in reversed(getattr(self, "_partial_tool_results", None) or []):
                blobs.append(str(tr.get("output") or "")[:800])
                if len(blobs) > 12:
                    break
        except Exception:
            pass
        for blob in blobs:
            city = _infer_city_from_text(blob)
            if city:
                return city
        # Bare profile strings that are just a city name
        for blob in blobs:
            s = re.sub(r"\s+", " ", str(blob or "").strip())
            if s and 2 <= len(s) <= 40 and len(s.split()) <= 3:
                if re.fullmatch(r"[A-Za-z][A-Za-z .'-]+", s):
                    # Avoid non-places
                    if s.lower() not in {"true", "false", "yes", "no", "owner", "user"}:
                        return s.split(",")[0].strip()
        return ""

    def process_query(
        self,
        user_input: str,
        include_memory: bool = True,
        callbacks: Optional[list] = None,
        thread_id: Optional[str] = None,
        source: Optional[str] = None,
        discord_user_info: Optional[Dict[str, Any]] = None,
        requested_approval_id: Optional[str] = None,
        cancel_event: Optional[threading.Event] = None,
        request_id: str = "",
        thinking_enabled: bool = True,
        reasoning_effort: str = "medium",
        caller_role: str = "",
    ) -> tuple:
        """Run one turn on the lean runtime (every channel uses this path).

        ``caller_role`` is owner / trusted / public. When it is not given it is
        worked out from the source (and the Discord user, for Discord).
        """
        from agent.adapters import get_adapter
        from agent.lean.runtime import run_lean_query

        if not caller_role:
            role = get_adapter(source).resolve_role(source, discord_user_info)
            caller_role = str(getattr(role, "value", role) or "public").lower()

        def emit(event: dict) -> None:
            for callback in list(callbacks or []):
                put = getattr(callback, "_put", None)
                if callable(put):
                    put(event)

        result = run_lean_query(
            self,
            message=user_input,
            session_id=str(thread_id or "default").strip() or "default",
            request_id=request_id,
            emit=emit,
            cancel=cancel_event or threading.Event(),
            source=str(source or "web"),
            thinking_enabled=thinking_enabled,
            reasoning_effort=reasoning_effort,
            caller_role=caller_role,
        )
        return str(result.get("response") or ""), bool(result.get("success"))
