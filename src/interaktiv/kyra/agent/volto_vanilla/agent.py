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
from langchain_core.messages import AnyMessage, SystemMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from interaktiv.kyra.agent.core.schemas import ContentNode, Site

logger = logging.getLogger(__name__)


def _format_node(node: ContentNode) -> str:
    """One-line summary: path — title (content_type) + description if available."""
    line = f"  {node.path} — {node.title} ({node.content_type})"
    if node.description:
        line += f" — {node.description}"
    return line


async def build_context(site: Site, page: str) -> str:
    """Build a context block with ancestors, siblings, and children."""
    parts: list[str] = []

    # Ancestors (breadcrumb).
    ancestors = await site.get_ancestors(page)
    if ancestors:
        parts.append("Elternhierarchie:")
        for node in ancestors:
            parts.append(_format_node(node))

    # Siblings (other children of the parent).
    parent = "/".join(page.rstrip("/").split("/")[:-1]) or "/"
    if parent != page:
        siblings = await site.get_children(parent, limit=25)
        others = [s for s in siblings if s.path != page]
        if others:
            parts.append("Geschwisterseiten:")
            for node in others:
                parts.append(_format_node(node))

    # Children.
    children = await site.get_children(page, limit=25)
    if children:
        parts.append("Unterseiten:")
        for node in children:
            parts.append(_format_node(node))

    return "\n".join(parts)


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
