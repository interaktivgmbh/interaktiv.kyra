"""LangGraph agent with site browsing and layout editing tools."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    AnyMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.message import MessagesState
from langgraph.graph.state import CompiledStateGraph

from interaktiv.kyra.agent.core.schemas import ContentNode, Site
from interaktiv.kyra.agent.core.tools import make_tools


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


def make_graph(
    llm: BaseChatModel,
    *,
    site: Site,
    current_page: str,
    prompt: str,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """Create an agent graph wired to a Site implementation."""
    site.set_current_page(current_page)

    return create_agent(
        llm,
        make_tools(site),
        system_prompt=SystemMessage(content=prompt),
        checkpointer=checkpointer,
    )


# ---------------------------------------------------------------------------
# Eager tool execution: stream LLM + fire tools as they complete
# ---------------------------------------------------------------------------


def _err_tool_msg(call_id: str, name: str, error: str) -> ToolMessage:
    """Return an error ToolMessage for a failed or unknown tool call."""
    content = json.dumps({"ok": False, "error": error}, ensure_ascii=False)
    return ToolMessage(content=content, tool_call_id=call_id, name=name)


async def _exec_tool(tool: BaseTool, call: dict) -> ToolMessage:
    """Execute a single tool call, returning a ToolMessage."""
    try:
        result = await tool.ainvoke(call["args"])
    except Exception as e:
        return _err_tool_msg(call["id"], call["name"], str(e))
    content = result if isinstance(result, list) else str(result)
    return ToolMessage(content=content, tool_call_id=call["id"], name=call["name"])


async def _stream_and_execute(
    llm: Runnable,
    messages: list,
    tools_by_name: dict[str, BaseTool],
    on_text_delta: Callable[[str], None] | None = None,
    on_tool_start: Callable[[str], None] | None = None,
) -> tuple[AIMessage, list[ToolMessage]]:
    """Stream LLM response and fire each tool call the moment its args are complete."""
    accumulated: AIMessageChunk | None = None
    call_data: dict[int, dict[str, str]] = {}
    fired: set[int] = set()
    tasks: dict[int, asyncio.Future[ToolMessage]] = {}

    def _try_fire(idx: int) -> None:
        if idx in fired or idx not in call_data:
            return
        cd = call_data[idx]
        if not cd["name"]:
            return
        try:
            args = json.loads(cd["args"]) if cd["args"] else {}
        except json.JSONDecodeError:
            return
        fired.add(idx)
        if on_tool_start:
            on_tool_start(cd["name"])
        tool = tools_by_name.get(cd["name"])
        if tool:
            tc = {"name": cd["name"], "id": cd["id"], "args": args}
            tasks[idx] = asyncio.create_task(_exec_tool(tool, tc))
        else:
            err = _err_tool_msg(cd["id"], cd["name"], f"Unknown tool: {cd['name']}")
            fut: asyncio.Future[ToolMessage] = asyncio.get_event_loop().create_future()
            fut.set_result(err)
            tasks[idx] = fut

    def _emit_text(chunk: AIMessageChunk) -> None:
        if not on_text_delta:
            return
        content = chunk.content
        if isinstance(content, str) and content:
            on_text_delta(content)
        elif isinstance(content, list):
            for part in content:
                if (
                    isinstance(part, dict)
                    and part.get("type") == "text"
                    and part.get("text")
                ):
                    on_text_delta(part["text"])

    async for chunk in llm.astream(messages):
        accumulated = chunk if accumulated is None else accumulated + chunk

        _emit_text(chunk)

        for tc_chunk in chunk.tool_call_chunks or []:
            idx: int = tc_chunk.get("index") or 0
            if idx not in call_data:
                for prev in call_data:
                    _try_fire(prev)
                call_data[idx] = {
                    "name": str(tc_chunk.get("name") or ""),
                    "id": str(tc_chunk.get("id") or ""),
                    "args": str(tc_chunk.get("args") or ""),
                }
            else:
                name = tc_chunk.get("name")
                if name:
                    call_data[idx]["name"] = str(name)
                tc_id = tc_chunk.get("id")
                if tc_id:
                    call_data[idx]["id"] = str(tc_id)
                call_data[idx]["args"] += str(tc_chunk.get("args") or "")

    for idx in call_data:
        _try_fire(idx)

    ai_msg = AIMessage(
        content=accumulated.content if accumulated else "",
        tool_calls=accumulated.tool_calls if accumulated else [],
        usage_metadata=getattr(accumulated, "usage_metadata", None),
        response_metadata=getattr(accumulated, "response_metadata", {}),
        id=accumulated.id if accumulated else None,
    )

    tool_msgs = [await tasks[idx] for idx in tasks]
    return ai_msg, tool_msgs


def make_eager_graph(
    llm: BaseChatModel,
    *,
    site: Site,
    current_page: str,
    prompt: str,
    checkpointer: BaseCheckpointSaver | None = None,
    on_text_delta: Callable[[str], None] | None = None,
    on_tool_start: Callable[[str], None] | None = None,
) -> CompiledStateGraph:
    """Agent graph that streams LLM output and fires tools eagerly."""
    site.set_current_page(current_page)
    tools = make_tools(site)
    tools_by_name = {t.name: t for t in tools}
    llm_with_tools = llm.bind_tools(tools)
    system_message = SystemMessage(content=prompt)

    async def agent(state: MessagesState) -> dict[str, list[AnyMessage]]:
        msgs = [system_message, *state["messages"]]
        ai_msg, tool_msgs = await _stream_and_execute(
            llm_with_tools, msgs, tools_by_name, on_text_delta, on_tool_start
        )
        return {"messages": [ai_msg, *tool_msgs]}

    def should_continue(state: MessagesState) -> str:
        last = state["messages"][-1]
        return "agent" if isinstance(last, ToolMessage) else END

    graph = StateGraph(MessagesState)
    graph.add_node("agent", agent)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", should_continue, {"agent": "agent", END: END})
    return graph.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# Input preparation
# ---------------------------------------------------------------------------


async def prepare_input(
    site: Site,
    *,
    messages: list[AnyMessage],
    current_page: str,
    include_context: bool = False,
) -> dict[str, list[AnyMessage]]:
    """Build graph input, optionally injecting page context."""
    prefix: list[AnyMessage] = []

    if current_page != site.current_page:
        site.set_current_page(current_page)
        prefix.append(
            SystemMessage(
                content=(
                    f"[Navigation] Du befindest dich jetzt auf der Seite "
                    f"'{current_page}'. Du kannst nur diese Seite bearbeiten. "
                    f"Andere Seiten kannst du lesen, aber nicht verändern."
                )
            )
        )

    if include_context:
        context = await build_context(site, current_page)
        if context:
            prefix.append(SystemMessage(content=f"[Seitenkontext]\n{context}"))

    if prefix:
        messages = [*prefix, *messages]
    return {"messages": messages}
