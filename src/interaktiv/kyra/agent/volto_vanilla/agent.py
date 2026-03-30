"""Factory for building a layout-editing agent graph."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

logger = logging.getLogger(__name__)

# Re-export from canonical location for backwards compatibility.
from interaktiv.kyra.agent.core.graph import build_context  # noqa: F401, E402


class RetryMiddleware(AgentMiddleware[Any, Any, Any]):
    """Retry failed model calls with exponential backoff."""

    def __init__(self, max_attempts: int = 3, base_delay: float = 1.0) -> None:
        self.max_attempts = max_attempts
        self.base_delay = base_delay

    @property
    def name(self) -> str:
        return "retry"

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        for attempt in range(1, self.max_attempts + 1):
            try:
                return handler(request)
            except Exception:
                if attempt == self.max_attempts:
                    raise
                delay = self.base_delay * (2 ** (attempt - 1)) + random.random()
                logger.warning(
                    "Model call failed (attempt %d/%d), retrying in %.1fs",
                    attempt,
                    self.max_attempts,
                    delay,
                )
                time.sleep(delay)
        raise RuntimeError("unreachable")

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        for attempt in range(1, self.max_attempts + 1):
            try:
                return await handler(request)
            except Exception:
                if attempt == self.max_attempts:
                    raise
                delay = self.base_delay * (2 ** (attempt - 1)) + random.random()
                logger.warning(
                    "Model call failed (attempt %d/%d), retrying in %.1fs",
                    attempt,
                    self.max_attempts,
                    delay,
                )
                await asyncio.sleep(delay)
        raise RuntimeError("unreachable")


def make_agent(
    llm: BaseChatModel,
    tools: list,
    prompt: str,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """Create a react agent graph wired to layout tools.

    Parameters
    ----------
    llm:
        Any LangChain chat model.
    tools:
        Tool list from ``make_tools`` (or a subset).
    prompt:
        System prompt that defines the agent's persona and instructions.
    checkpointer:
        Optional LangGraph checkpointer for conversation persistence.

    Returns
    -------
    CompiledStateGraph
        A compiled LangGraph graph, ready to ``.invoke()`` or ``.stream()``.
    """
    system_message = SystemMessage(content=prompt)
    middleware: list[AgentMiddleware[Any, Any, Any]] = [RetryMiddleware()]

    return create_agent(
        llm,
        tools,
        system_prompt=system_message,
        middleware=middleware,
        checkpointer=checkpointer,
    )
