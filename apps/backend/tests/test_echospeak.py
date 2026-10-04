"""
Tests for Echo Speak.
Pytest test suite for validating the voice AI system.
"""

import asyncio
import os
import sys
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime
from tests.route_paths import route_paths as _route_paths
import api.deps as deps

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _bind_disposable_file_scope(agent, root) -> None:
    """Give parser tests an explicit disposable filesystem authority root."""
    from agent.tools import set_active_project_root

    set_active_project_root(str(root))
    agent._execution_context.project_path = str(root)
    agent._execution_context.workspace_root = str(root)
    agent._state_store.update_thread_state(
        agent._thread_key(),
        project_path=str(root),
        workspace_root=str(root),
    )


class TestConfig:
    """Tests for the configuration module."""

    def test_config_creation(self):
        """Test that config can be created."""
        from config import config

        assert config is not None
        assert hasattr(config, 'openai')
        assert hasattr(config, 'local')
        assert hasattr(config, 'embedding')
        assert hasattr(config, 'voice')
        assert hasattr(config, 'api')

    def test_config_openai(self):
        """Test OpenAI configuration."""
        from config import config

        assert config.openai.model == "gpt-4o-mini"
        assert isinstance(config.openai.temperature, float)
        assert isinstance(config.openai.max_tokens, int)

    def test_config_local(self):
        """Test local model configuration."""
        from config import config

        assert config.local.provider.value in ["ollama", "lmstudio", "localai", "llama_cpp", "vllm"]
        assert isinstance(config.local.temperature, float)

    def test_memory_path_exists(self):
        """Test that memory path is set correctly."""
        from config import config, MEMORY_DIR

        assert config.memory_path.exists() or str(MEMORY_DIR)


class TestModelProvider:
    """Tests for model provider enum."""

    def test_provider_values(self):
        """Test all provider values exist."""
        from config import ModelProvider

        assert ModelProvider.OPENAI.value == "openai"
        assert ModelProvider.OLLAMA.value == "ollama"
        assert ModelProvider.LM_STUDIO.value == "lmstudio"
        assert ModelProvider.LOCALAI.value == "localai"
        assert ModelProvider.LLAMA_CPP.value == "llama_cpp"
        assert ModelProvider.VLLM.value == "vllm"


class TestModelRuntimeClient:
    """Tests for the canonical provider communication client."""

    @pytest.fixture
    def mock_openai(self):
        """Mock OpenAI dependencies."""
        with patch('agent.model_runtime.ReasoningChatOpenAI') as mock:
            mock_instance = MagicMock()
            mock.return_value = mock_instance
            yield mock_instance

    @pytest.fixture
    def mock_ollama(self):
        """Mock Ollama dependencies."""
        with patch('agent.model_runtime.ChatOllama') as mock:
            mock_instance = MagicMock()
            mock.return_value = mock_instance
            yield mock_instance

    def test_openai_runtime_creation(self, mock_openai):
        """Test OpenAI provider client creation with an exact model."""
        from agent.model_runtime import ModelRuntimeClient
        from config import ModelProvider

        runtime = ModelRuntimeClient(ModelProvider.OPENAI, "gpt-4o-mini")
        assert runtime.provider == ModelProvider.OPENAI
        assert runtime.model_id == "gpt-4o-mini"

    def test_ollama_runtime_creation(self, mock_ollama):
        """Test Ollama provider client creation with an exact model."""
        from agent.model_runtime import ModelRuntimeClient
        from config import ModelProvider

        runtime = ModelRuntimeClient(ModelProvider.OLLAMA, "llama3")
        assert runtime.provider == ModelProvider.OLLAMA
        assert runtime.model_id == "llama3"


class TestAgentMemory:
    """Tests for the agent memory module."""

    @pytest.fixture
    def mock_dependencies(self):
        """Mock external dependencies."""
        with patch('agent.memory.OpenAIEmbeddings') as mock_embed:
            with patch('agent.memory.FAISS') as mock_faiss:
                mock_store = MagicMock()
                mock_faiss.from_texts.return_value = mock_store
                mock_faiss.load_local.return_value = mock_store
                yield mock_embed, mock_faiss, mock_store

    @pytest.fixture
    def patch_memory_config(self, monkeypatch):
        from config import config

        monkeypatch.setattr(config.openai, "api_key", "test-key", raising=False)
        monkeypatch.setattr(config, "memory_partition_enabled", False, raising=False)
        return config

    def test_memory_initialization(self, mock_dependencies, patch_memory_config, tmp_path):
        """Test memory initialization."""
        from agent.memory import AgentMemory
        _mock_embed, _mock_faiss, mock_store = mock_dependencies

        memory = AgentMemory(memory_path=str(tmp_path))

        assert memory is not None
        assert memory.vector_store is mock_store

    def test_add_conversation(self, mock_dependencies, patch_memory_config, tmp_path):
        """Test adding conversation to memory."""
        from agent.memory import AgentMemory
        _mock_embed, _mock_faiss, mock_store = mock_dependencies

        memory = AgentMemory(memory_path=str(tmp_path))

        memory.add_conversation("Hello", "Hi there!")
        mock_store.add_texts.assert_called()

    def test_retrieve_relevant(self, mock_dependencies, patch_memory_config, tmp_path):
        """Test retrieving relevant memories."""
        from agent.memory import AgentMemory
        _mock_embed, _mock_faiss, mock_store = mock_dependencies

        mock_doc = MagicMock()
        mock_doc.page_content = "User: Hello\nAI: Hi there!"
        mock_doc.metadata = {}

        memory = AgentMemory(memory_path=str(tmp_path))
        mock_store.similarity_search.return_value = [mock_doc]

        results = memory.retrieve_relevant("Hello", k=5)

        assert isinstance(results, list)
        assert results == [mock_doc]

    def test_add_conversation_partitioned(self, mock_dependencies, patch_memory_config, tmp_path, monkeypatch):
        """Test partitioned memory storage paths."""
        from config import config
        from agent.memory import AgentMemory

        monkeypatch.setattr(config, "memory_partition_enabled", True, raising=False)
        _mock_embed, _mock_faiss, mock_store = mock_dependencies

        memory = AgentMemory(memory_path=str(tmp_path))
        memory.add_conversation("Hello", "Hi!")

        expected_path = memory._namespace_dir("general", "default")
        assert str(expected_path) in memory._vector_stores
        assert memory._vector_stores[str(expected_path)] is mock_store


class TestTools:
    """Tests for the tools module."""

    def test_web_search_tool_exists(self):
        """Test web search tool is available."""
        from agent.tools import web_search

        assert getattr(web_search, "name", "") == "web_search"
        assert hasattr(web_search, "invoke")

    def test_analyze_screen_tool_exists(self):
        """Test analyze screen tool is available."""
        from agent.tools import analyze_screen

        assert getattr(analyze_screen, "name", "") == "analyze_screen"
        assert hasattr(analyze_screen, "invoke")

    def test_get_system_time_tool_exists(self):
        """Test get system time tool is available."""
        from agent.tools import get_system_time

        assert getattr(get_system_time, "name", "") == "get_system_time"
        assert hasattr(get_system_time, "invoke")

    def test_calculate_tool_exists(self):
        """Test calculate tool is available."""
        from agent.tools import calculate

        assert getattr(calculate, "name", "") == "calculate"
        assert hasattr(calculate, "invoke")

    def test_get_available_tools(self):
        """Test getting list of available tools."""
        from agent.tools import get_available_tools

        tools = get_available_tools()
        assert isinstance(tools, list)
        assert len(tools) > 0

    def test_web_search_timeout_returns_timeout_message(self, monkeypatch):
        """Configured HTTP-provider timeouts should surface as provider errors."""
        import builtins
        import requests
        from agent.tools import web_search
        from config import config

        def fake_get(*args, **kwargs):
            raise requests.exceptions.Timeout()

        real_import = builtins.__import__

        def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name in {"ddgs", "duckduckgo_search"} or (fromlist and name == "ddgs"):
                raise ImportError("blocked in unit test")
            return real_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr(config, "web_search_provider", "brave", raising=False)
        monkeypatch.setattr(config, "brave_search_api_key", "bsa-test", raising=False)
        monkeypatch.setattr(config, "searxng_base_url", "", raising=False)
        monkeypatch.setattr(config, "web_search_timeout", 7, raising=False)
        monkeypatch.setattr(requests, "get", fake_get, raising=False)
        monkeypatch.setattr(builtins, "__import__", guarded_import)

        out = web_search.invoke({"query": "Edmonton Oilers score right now"})

        assert "timed out after 7s" in str(out).lower()

class TestDiscordHardening:

    def test_discord_followup_context_skips_smalltalk_with_suspicious_history(self):
        from discord_bot import EchoSpeakDiscordBot

        class StubAuthor:
            def __init__(self, name: str):
                self.name = name
                self.display_name = name

        class StubMessage:
            def __init__(self, message_id: int, author, content: str):
                self.id = message_id
                self.author = author
                self.content = content

        class StubChannel:
            def __init__(self, messages):
                self._messages = messages

            async def history(self, limit=10, oldest_first=False):
                for msg in self._messages[:limit]:
                    yield msg

        user = StubAuthor("pi")
        bot_user = StubAuthor("EchoSpeak")
        prior_messages = [
            StubMessage(2, bot_user, "Hey. I'm right here."),
            StubMessage(1, user, "i am mem0s new account"),
        ]

        bot = EchoSpeakDiscordBot("x" * 60, lambda **_: ("ok", True))
        bot.client = type("StubClient", (), {"user": bot_user})()
        live_message = type("LiveMessage", (), {})()
        live_message.id = 3
        live_message.author = user
        live_message.channel = StubChannel(prior_messages)

        ctx = asyncio.run(bot._maybe_get_followup_context(live_message, "yo"))

        assert ctx == ""


    def test_coding_path_pin_and_stub_rejection(self, tmp_path, monkeypatch):
        """Bare relative paths resolve under active project root (discovery-based pin)."""
        from agent import tools as tools_mod
        from agent.tools import (
            set_active_project_root,
            _safe_file_path,
            _looks_like_code_stub,
            get_active_project_root,
            _file_tool_root,
        )

        # Use a temp "desktop" project — not a hard-coded product name
        proj = tmp_path / "any-project-xyz"
        proj.mkdir()
        set_active_project_root(str(proj))
        pinned = get_active_project_root()
        assert pinned is not None
        assert pinned.resolve() == proj.resolve()
        resolved = _safe_file_path("index.html")
        assert resolved is not None
        assert resolved == (proj / "index.html").resolve()
        # Must not land in EchoSpeak repo root
        assert resolved.resolve() != (_file_tool_root() / "index.html").resolve()
        # Stub rejection (generic code quality gate)
        assert _looks_like_code_stub("game.js", "// Implement collision detection…") is True
        # Short real code is a legitimate file; only placeholders are stubs.
        assert _looks_like_code_stub("hello.py", 'print("hi")') is False
        big = "function loop(){ requestAnimationFrame(loop); }\n" * 20
        assert _looks_like_code_stub("game.js", big) is False
        set_active_project_root(None)


    def test_discord_server_access_can_be_granted_by_role(self, monkeypatch):
        from config import config
        from discord_bot import EchoSpeakDiscordBot

        class StubRole:
            def __init__(self, name: str, role_id: str):
                self.name = name
                self.id = role_id

        class StubAuthor:
            def __init__(self, user_id: str, roles: list[StubRole]):
                self.id = user_id
                self.roles = roles

        monkeypatch.setattr(config, "discord_bot_owner_id", "owner-1", raising=False)
        monkeypatch.setattr(config, "discord_bot_trusted_users", [], raising=False)
        monkeypatch.setattr(config, "discord_bot_allowed_users", [], raising=False)
        monkeypatch.setattr(config, "discord_bot_allowed_roles", ["Echo Access"], raising=False)

        bot = EchoSpeakDiscordBot("x" * 60, lambda **_: ("ok", True))
        access_ok, reason, role_names, role_ids = asyncio.run(
            bot._invocation_access(
                StubAuthor("2002", [StubRole("Echo Access", "55")]),
                is_dm=False,
            )
        )

        assert access_ok is True
        assert reason == "allowed_role"
        assert role_names == ["Echo Access"]
        assert role_ids == ["55"]

    def test_discord_dm_access_can_be_granted_by_verified_mutual_role(self, monkeypatch):
        from config import config
        from discord_bot import EchoSpeakDiscordBot

        class StubRole:
            def __init__(self, name: str, role_id: str):
                self.name = name
                self.id = role_id

        class StubMember:
            def __init__(self, roles: list[StubRole]):
                self.roles = roles

        class StubGuild:
            def __init__(self, member: StubMember | None):
                self._member = member

            def get_member(self, _user_id: int):
                return None

            async def fetch_member(self, _user_id: int):
                if self._member is None:
                    raise LookupError("missing")
                return self._member

        class StubClient:
            def __init__(self, guilds: list[StubGuild]):
                self.guilds = guilds

        class StubAuthor:
            def __init__(self, user_id: str):
                self.id = user_id
                self.roles = []

        monkeypatch.setattr(config, "discord_bot_owner_id", "", raising=False)
        monkeypatch.setattr(config, "discord_bot_trusted_users", [], raising=False)
        monkeypatch.setattr(config, "discord_bot_allowed_users", [], raising=False)
        monkeypatch.setattr(config, "discord_bot_allowed_roles", ["Echo Access"], raising=False)

        bot = EchoSpeakDiscordBot("x" * 60, lambda **_: ("ok", True))
        bot.client = StubClient([StubGuild(StubMember([StubRole("Echo Access", "55")]))])
        access_ok, reason, role_names, role_ids = asyncio.run(
            bot._invocation_access(
                StubAuthor("3003"),
                is_dm=True,
            )
        )

        assert access_ok is True
        assert reason == "verified_allowed_role_dm"
        assert role_names == ["Echo Access"]
        assert role_ids == ["55"]

    def test_discord_dm_without_matching_role_is_denied_when_role_gate_configured(self, monkeypatch):
        from config import config
        from discord_bot import EchoSpeakDiscordBot

        class StubRole:
            def __init__(self, name: str, role_id: str):
                self.name = name
                self.id = role_id

        class StubMember:
            def __init__(self, roles: list[StubRole]):
                self.roles = roles

        class StubGuild:
            def __init__(self, member: StubMember | None):
                self._member = member

            def get_member(self, _user_id: int):
                return None

            async def fetch_member(self, _user_id: int):
                if self._member is None:
                    raise LookupError("missing")
                return self._member

        class StubClient:
            def __init__(self, guilds: list[StubGuild]):
                self.guilds = guilds

        class StubAuthor:
            def __init__(self, user_id: str):
                self.id = user_id
                self.roles = []

        monkeypatch.setattr(config, "discord_bot_owner_id", "", raising=False)
        monkeypatch.setattr(config, "discord_bot_trusted_users", [], raising=False)
        monkeypatch.setattr(config, "discord_bot_allowed_users", [], raising=False)
        monkeypatch.setattr(config, "discord_bot_allowed_roles", ["Echo Access"], raising=False)

        bot = EchoSpeakDiscordBot("x" * 60, lambda **_: ("ok", True))
        bot.client = StubClient([StubGuild(StubMember([StubRole("Different Role", "99")]))])
        access_ok, reason, role_names, role_ids = asyncio.run(
            bot._invocation_access(
                StubAuthor("4004"),
                is_dm=True,
            )
        )

        assert access_ok is False
        assert reason == "dm_not_allowlisted"
        assert role_names == ["Different Role"]
        assert role_ids == ["99"]

class TestUpdateContextParity:
    def test_twitter_autonomous_prompt_uses_shared_update_context_service(self, monkeypatch):
        from twitter_bot import EchoSpeakTwitterBot, _NO_TWEET_SENTINEL
        from agent.update_context import get_update_context_service

        bot = EchoSpeakTwitterBot()
        bot._agent = object()

        monkeypatch.setattr("twitter_bot._load_auto_tweet_state", lambda: {"tweets_today": [], "recent_hashes": []})

        service = get_update_context_service()
        monkeypatch.setattr(
            service,
            "build_context_block",
            lambda **_kwargs: "SHARED_UPDATE_CONTEXT_BLOCK",
        )

        captured = {}

        def fake_generate(prompt: str) -> str:
            captured["prompt"] = prompt
            return _NO_TWEET_SENTINEL

        bot._generate_tweet_agentic = fake_generate

        bot._autonomous_tweet_tick(max_daily=5)

        assert "SHARED_UPDATE_CONTEXT_BLOCK" in captured["prompt"]



class TestToolAllowlistMerge:
    def test_skills_cannot_expand_or_shrink_workspace_ceiling(self):
        from agent.skills_registry import merge_tool_allowlists

        # Workspace ceiling only allows file_read.
        workspace = ["file_read"]
        # Skill tries to allow terminal_run, but must not hide workspace-safe file_read.
        skills = [["terminal_run"]]
        merged = merge_tool_allowlists(workspace, skills)
        assert merged == {"file_read"}


class TestEmbeddingsConfig:
    def test_lm_studio_embeddings_disable_tiktoken(self, monkeypatch, tmp_path):
        from config import config, ModelProvider

        # Force LM Studio embeddings config.
        monkeypatch.setattr(config.embedding, "provider", ModelProvider.LM_STUDIO, raising=False)
        monkeypatch.setattr(config.embedding, "model", "text-embedding-nomic-embed-text-v1.5", raising=False)
        monkeypatch.setattr(config.local, "base_url", "http://localhost:1234", raising=False)

        captured_kwargs = {}

        class StubEmbeddings:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)

            def embed_query(self, _text: str):
                return [0.0]

        class StubStore:
            def save_local(self, _path: str):
                return None

        class StubFAISS:
            @staticmethod
            def load_local(*args, **kwargs):
                return StubStore()

            @staticmethod
            def from_texts(*args, **kwargs):
                return StubStore()

        import agent.memory as memory_mod

        monkeypatch.setattr(memory_mod, "OpenAIEmbeddings", StubEmbeddings, raising=True)
        monkeypatch.setattr(memory_mod, "FAISS", StubFAISS, raising=True)

        from agent.memory import AgentMemory

        AgentMemory(memory_path=str(tmp_path))
        assert captured_kwargs.get("tiktoken_enabled") is False


class TestVoiceIO:
    """Tests for the browser-only voice posture."""

    def test_local_stt_engine_is_removed(self):
        from io_module.stt_engine import get_stt_engine

        with pytest.raises(RuntimeError, match="browser speech recognition"):
            get_stt_engine()

    def test_pocket_tts_factory_is_removed(self):
        from io_module.pocket_tts_engine import get_pocket_tts_engine

        with pytest.raises(RuntimeError, match="browser speech playback"):
            get_pocket_tts_engine()

    def test_pocket_tts_class_is_removed(self):
        from io_module.pocket_tts_engine import PocketTTSEngine

        with pytest.raises(RuntimeError, match="browser speech playback"):
            PocketTTSEngine()


class TestVisionIO:
    """Tests for the vision I/O module."""

    def test_capture_screen_function_exists(self):
        """Test capture_screen function exists."""
        from io_module.vision import capture_screen

        assert callable(capture_screen)

    def test_perform_ocr_function_exists(self):
        """Test perform_ocr function exists."""
        from io_module.vision import perform_ocr

        assert callable(perform_ocr)

    def test_analyze_screen_content_function_exists(self):
        """Test analyze_screen_content function exists."""
        from io_module.vision import analyze_screen_content

        assert callable(analyze_screen_content)


class TestAPI:
    """Tests for the FastAPI server module."""

    def test_server_module_exists(self):
        """Test server module can be imported."""
        from api import server

        assert server is not None

    def test_app_exists(self):
        """Test FastAPI app is created."""
        from api.server import app

        assert app is not None

    def test_app_routes_exist(self):
        """Test FastAPI exposes a routes collection."""
        from api.server import app

        assert app.routes is not None

    def test_thread_scoped_agents_do_not_manage_background_services(self, monkeypatch):
        import agent.core as core_mod
        from api import server
        from config import config

        created: list[dict] = []

        class StubAgent:
            def __init__(self, memory_path=None, llm_provider=None, manage_background_services=True, **_kwargs):
                created.append(
                    {
                        "provider": llm_provider,
                        "manage_background_services": manage_background_services,
                    }
                )

        monkeypatch.setattr(config, "multi_agent_enabled", True, raising=False)
        monkeypatch.setattr(config, "use_local_models", False, raising=False)
        monkeypatch.setattr(server, "_agent", None, raising=False)
        monkeypatch.setattr(server, "_runtime_provider", None, raising=False)
        monkeypatch.setattr(core_mod, "EchoSpeakAgent", StubAgent, raising=True)

        with deps._agent_pool_lock:
            deps._agent_pool.clear()

        deps.get_agent("thread-x")
        deps.get_agent(None)

        assert len(created) == 2
        assert created[0]["manage_background_services"] is False
        assert created[1]["manage_background_services"] is True

    def test_query_endpoint_exists(self):
        """Test query endpoint is defined."""
        from api.server import app

        route_paths = _route_paths(app)
        assert "/query" in route_paths

    def test_health_endpoint_exists(self):
        """Test health endpoint is defined."""
        from api.server import app

        route_paths = _route_paths(app)
        assert "/health" in route_paths

    def test_provider_endpoint_exists(self):
        """Test provider endpoint is defined."""
        from api.server import app

        route_paths = _route_paths(app)
        assert "/provider" in route_paths


class TestCoreAgent:
    """Tests for the core agent module."""

    def test_core_module_exists(self):
        """Test core module can be imported."""
        from agent import core

        assert core is not None

    def test_echo_speak_agent_class_exists(self):
        """Test EchoSpeakAgent class exists."""
        from agent.core import EchoSpeakAgent

        assert EchoSpeakAgent is not None

    def test_create_agent_function_exists(self):
        """Test create_agent function exists."""
        from app import create_agent

        assert callable(create_agent)

    def test_list_available_providers(self):
        """Test listing available providers."""
        from agent.model_runtime import list_available_providers

        providers = list_available_providers()
        assert isinstance(providers, list)
        assert len(providers) > 0

    def test_get_provider_requirements(self):
        """Test getting provider requirements."""
        from agent.model_runtime import get_provider_requirements
        from config import ModelProvider

        reqs = get_provider_requirements(ModelProvider.OLLAMA)
        assert "env_vars" in reqs
        assert "pip_packages" in reqs


class TestIntegration:
    """Integration tests for the complete system."""

    def test_config_imports(self):
        """Test all config values are accessible."""
        from config import config

        assert isinstance(config.voice.rate, int)
        assert isinstance(config.voice.volume, float)
        assert isinstance(config.memory_path, object)

    def test_tools_can_be_imported(self):
        """Test all tools can be imported."""
        from agent.tools import (
            web_search,
            analyze_screen,
            get_system_time,
            calculate,
            take_screenshot,
            get_available_tools
        )

        tools = get_available_tools()
        assert isinstance(tools, list)
        assert len(tools) > 0


class TestLocalModels:
    """Tests for local model functionality."""

    def test_model_runtime_catalog_supports_all_providers(self):
        """Test that the provider communication layer catalogs all providers."""
        from agent.model_runtime import list_available_providers
        from config import ModelProvider

        providers = [
            ModelProvider.OPENAI,
            ModelProvider.OLLAMA,
            ModelProvider.LM_STUDIO,
            ModelProvider.LOCALAI,
        ]

        catalog = {entry["id"] for entry in list_available_providers()}
        for provider in providers:
            assert provider.value in catalog

    def test_model_provider_enum(self):
        """Test model provider enum values."""
        from config import ModelProvider

        assert ModelProvider.OPENAI.value == "openai"
        assert ModelProvider.OLLAMA.value == "ollama"
        assert ModelProvider.LM_STUDIO.value == "lmstudio"
        assert ModelProvider.LOCALAI.value == "localai"
        assert ModelProvider.LLAMA_CPP.value == "llama_cpp"
        assert ModelProvider.VLLM.value == "vllm"

    def test_local_config_structure(self):
        """Test local model config has all required fields."""
        from config import config

        local = config.local
        assert hasattr(local, 'provider')
        assert hasattr(local, 'base_url')
        assert hasattr(local, 'model_name')
        assert hasattr(local, 'temperature')


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
