"""EchoSpeak lean runtime.

One simple agent loop modelled on Hermes Agent:

    system prompt + real history + user message
      -> model (streamed: reasoning, text, native tool calls)
      -> run the requested tools (read-only ones in parallel)
      -> feed results back
      -> repeat until the model answers without calling a tool

The model decides when the work is done. The runtime only enforces real
safety boundaries: permission flags, filesystem roots, and approval for
destructive or outward-facing actions.
"""

from agent.lean.settings import lean_runtime_enabled

__all__ = ["lean_runtime_enabled"]
