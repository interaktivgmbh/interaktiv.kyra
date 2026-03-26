"""Direct Layout Agent integration — replaces the HTTP proxy.

Provides Plone REST service classes with the same API contract as
``ai_edit_proxy.py`` but runs the LangGraph agent in-process via a
dedicated asyncio event loop on a daemon thread.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import threading
import uuid
from typing import Any

from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from plone import api
from plone.protect.interfaces import IDisableCSRFProtection
from plone.restapi.services import Service
from zope.interface import alsoProvides

from interaktiv.kyra.agent.stock_photos import make_stock_photo_tools
from interaktiv.kyra.agent.volto_vanilla.agent import make_agent
from interaktiv.kyra.agent.volto_vanilla.converter import volto_to_page_state
from interaktiv.kyra.agent.volto_vanilla.engine import Engine
from interaktiv.kyra.agent.volto_vanilla.reverse_converter import layout_to_volto
from interaktiv.kyra.agent.volto_vanilla.tools import Permission, make_tools
from interaktiv.kyra.registry.ai_assistant import IAIAssistantSchema
from interaktiv.kyra.services.ai_edit_prompts import load_prompt, progress_message
from interaktiv.kyra.services.ai_edit_storage import (
    Conversation,
    Job,
    conversations,
    jobs,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dedicated asyncio event loop (daemon thread)
# ---------------------------------------------------------------------------

_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()
checkpointer = MemorySaver()


def _get_loop() -> asyncio.AbstractEventLoop:
    """Return the shared asyncio event loop, creating it on first call."""
    global _loop
    if _loop is not None and _loop.is_running():
        return _loop
    with _loop_lock:
        if _loop is not None and _loop.is_running():
            return _loop
        _loop = asyncio.new_event_loop()

        def _run(loop: asyncio.AbstractEventLoop) -> None:
            asyncio.set_event_loop(loop)
            loop.run_forever()

        t = threading.Thread(target=_run, args=(_loop,), daemon=True)
        t.start()
        return _loop


# ---------------------------------------------------------------------------
# LLM factory
# ---------------------------------------------------------------------------


def _make_llm(model: str, api_key: str) -> Any:
    kwargs: dict[str, Any] = dict(
        model=model,
        api_key=api_key,  # type: ignore[arg-type]
        temperature=0.5,
        max_tokens=16384,  # pyright: ignore[reportCallIssue]
        max_retries=3,
    )
    # Reasoning parameters only for o-series models (o1, o3, o4-mini, etc.)
    if model.startswith("o"):
        kwargs["use_responses_api"] = True
        kwargs["reasoning"] = {"effort": "low"}
    return ChatOpenAI(**kwargs)


def _get_openai_config() -> tuple[str, str]:
    """Return (api_key, model) from Plone registry."""
    api_key = (
        api.portal.get_registry_record(
            name="openai_api_key", interface=IAIAssistantSchema
        )
        or ""
    )
    model = (
        api.portal.get_registry_record(
            name="openai_model", interface=IAIAssistantSchema
        )
        or ""
    )
    return api_key, model


# ---------------------------------------------------------------------------
# Reference pages section builder
# ---------------------------------------------------------------------------


def _build_reference_pages_section(ref_engines: dict[str, Engine] | None) -> str:
    """Build a dynamic prompt section about reference pages.

    Always appended to the prompt -- either lists available pages or
    explicitly states that none are available (to prevent hallucination).
    """
    lines: list[str] = ["\n\n## Referenzseiten\n"]
    if not ref_engines:
        lines.append(
            "In dieser Konversation stehen keine Referenzseiten zur Verfügung. "
            "Du kannst nur die aktuelle Arbeitsseite sehen und bearbeiten."
        )
        return "\n".join(lines)

    lines.append(
        "In dieser Konversation hast du Zugriff auf Referenzseiten — "
        "bestehende Seiten derselben Website, die du lesen, aber nicht "
        "verändern kannst. Nutze `get_layout` und `get_metadata` mit dem "
        "`page`-Parameter, um eine Referenzseite zu lesen. "
        "Orientiere dich an diesen Seiten für Stil, Tonalität, "
        "Formulierungen und Verlinkung.\n\n"
        "Verfügbare Referenzseiten:\n"
    )
    for link, eng in ref_engines.items():
        meta = eng.metadata
        desc = meta.description
        if meta.title and desc:
            lines.append(f"- `{link}` — {meta.title}: {desc}")
        elif meta.title:
            lines.append(f"- `{link}` — {meta.title}")
        else:
            lines.append(f"- `{link}`")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Context note builder
# ---------------------------------------------------------------------------


def _build_context_note(ctx: dict[str, Any], conv: Conversation) -> str:
    """Turn a context dict into an agent-facing note."""
    parts: list[str] = []

    block_id = ctx.get("block_id")
    if block_id is not None:
        found = conv.engine.find_block_by_id(block_id)
        if found is not None:
            path, name, block_type = found
            parts.append(
                f"Der Nutzer bezieht sich auf den Block '{name}' "
                f"(Pfad: {path}, Typ: {block_type})."
            )

    text = ctx.get("text")
    if text is not None:
        parts.append(f"Der Nutzer hat folgenden Text markiert: '{text}'")

    if not parts:
        return ""
    return "[Kontext: " + " ".join(parts) + "]"


# ---------------------------------------------------------------------------
# Background agent coroutine
# ---------------------------------------------------------------------------


async def _run_agent(job: Job, conv: Conversation, user_message: str) -> None:
    """Run the agent and update job status."""
    snapshot = conv.engine.get_page_state()
    try:
        last_message = ""
        async for chunk in conv.agent.astream(
            {"messages": [("user", user_message)]},
            conv.config,  # type: ignore[arg-type]
            stream_mode="updates",
        ):
            for node, updates in chunk.items():
                if node == "model":
                    msg = updates["messages"][-1]
                    if hasattr(msg, "content") and msg.content:
                        content = msg.content
                        if isinstance(content, list):
                            content = "".join(
                                b["text"]
                                for b in content
                                if isinstance(b, dict) and b.get("type") == "text"
                            )
                        if content:
                            last_message = content
                elif node == "tools":
                    for msg in updates["messages"]:
                        job.progress = progress_message(msg.name, conv.language)

        # Detect what changed: metadata, layout, or both
        current = conv.engine.get_page_state()
        metadata_changed = current.metadata != snapshot.metadata
        layout_changed = current.layout != snapshot.layout

        job.message = last_message
        if layout_changed:
            job.state = layout_to_volto(
                current.layout,
                current.metadata,
                original_volto=conv.original_volto,
                original_layout=conv.original_layout,
            )
        elif metadata_changed:
            job.state = {
                k: v
                for k, v in current.metadata.model_dump().items()
                if v != "" and v != []
            }
        conv.first_message = False
        job.status = "completed"

    except asyncio.CancelledError:
        conv.engine.replace_state(snapshot)
        job.status = "cancelled"
    except Exception as exc:
        logger.exception("[ai-edit-engine] Agent run failed")
        conv.engine.replace_state(snapshot)
        job.error = str(exc)
        job.status = "failed"


# ---------------------------------------------------------------------------
# Base service class
# ---------------------------------------------------------------------------


class _EngineServiceBase(Service):
    """Shared base: disables CSRF, provides JSON body helper."""

    def __init__(self, context, request):
        super().__init__(context, request)
        alsoProvides(self.request, IDisableCSRFProtection)

    def _read_body(self) -> dict[str, Any]:
        return json.loads(self.request.get("BODY", "{}"))


# ---------------------------------------------------------------------------
# POST @ai-edit-create — create conversation
# ---------------------------------------------------------------------------


class AIEditCreateConversation(_EngineServiceBase):

    def reply(self) -> dict[str, Any]:
        body = self._read_body()

        schema_name = body.get("schema", "")
        version = body.get("version", "")
        if schema_name != "volto" or version != "vanilla":
            self.request.response.setStatus(422)
            return {
                "error": f"Unsupported schema/version: {schema_name}/{version}."
            }

        state = body.get("state")
        if not state:
            self.request.response.setStatus(400)
            return {"error": "state is required"}

        permissions: list[Permission] = body.get("permissions", [])
        language: str = body.get("language", "de")
        reference_pages: list[dict[str, Any]] = body.get("reference_pages", [])

        # Convert main state
        try:
            page_state = volto_to_page_state(state)
        except Exception as exc:
            self.request.response.setStatus(400)
            return {"error": f"Invalid state: {exc}"}

        # Convert reference pages to IR
        ref_engines: dict[str, Engine] = {}
        seen_links: set[str] = set()
        for i, ref_data in enumerate(reference_pages):
            try:
                ref_state = volto_to_page_state(ref_data)
            except Exception as exc:
                self.request.response.setStatus(400)
                return {"error": f"Invalid reference_pages[{i}]: {exc}"}
            link = ref_state.metadata.link
            if not link:
                self.request.response.setStatus(400)
                return {
                    "error": f"reference_pages[{i}] is missing a 'link' field."
                }
            if link in seen_links:
                self.request.response.setStatus(400)
                return {"error": f"Duplicate reference page link: '{link}'."}
            seen_links.add(link)
            ref_engines[link] = Engine.from_page_state(ref_state)

        ref_pages = ref_engines or None
        read_only = not permissions
        engine = Engine.from_page_state(page_state)

        if read_only:
            tools: list = []
        else:
            tools = make_tools(
                engine,
                permissions=permissions,
                reference_engines=ref_pages,
            )
            if "create" in permissions:
                tools.extend(make_stock_photo_tools())

        # Load prompt + reference section
        try:
            prompt = load_prompt(tuple(sorted(permissions)))
        except ValueError as exc:
            self.request.response.setStatus(422)
            return {"error": str(exc)}
        prompt += _build_reference_pages_section(ref_pages)

        # LLM
        api_key, model = _get_openai_config()
        if not api_key or not model:
            self.request.response.setStatus(501)
            return {"error": "OpenAI API key or model not configured in Plone registry"}

        # Use weaker model for read-only conversations if a secondary model
        # were configured; for now we always use the single configured model.
        llm = _make_llm(model, api_key)

        thread_id = str(uuid.uuid4())
        agent = make_agent(
            llm,
            tools,
            prompt,
            checkpointer=checkpointer,
        )

        conversation_id = str(uuid.uuid4())
        conv = Conversation(
            conversation_id=conversation_id,
            schema_name=schema_name,
            version=version,
            language=language,
            read_only=read_only,
            engine=engine,
            original_volto=state,
            original_layout=copy.deepcopy(page_state.layout),
            agent=agent,
            config={
                "configurable": {"thread_id": thread_id},
                "max_concurrency": 1,
            },
            reference_engines=ref_engines,
        )
        conversations.create(conv)

        self.request.response.setStatus(201)
        return {"conversation_id": conversation_id}


# ---------------------------------------------------------------------------
# POST @ai-edit-message — send message, start background agent job
# ---------------------------------------------------------------------------


class AIEditSendMessage(_EngineServiceBase):

    def reply(self) -> dict[str, Any]:
        body = self._read_body()
        conversation_id = body.pop("conversation_id", None)
        if not conversation_id:
            self.request.response.setStatus(400)
            return {"error": "conversation_id is required"}

        conv = conversations.get(conversation_id)
        if conv is None:
            self.request.response.setStatus(404)
            return {"error": "Conversation not found."}

        if conv.active_job is not None and conv.active_job.status == "running":
            self.request.response.setStatus(409)
            return {
                "error": "A message is already being processed for this conversation."
            }

        message = body.get("message", "")
        if not message:
            self.request.response.setStatus(400)
            return {"error": "message is required"}

        # Optional state override -- detect external changes
        state_changed_externally = False
        new_state = body.get("state")
        if new_state is not None:
            try:
                new_page_state = volto_to_page_state(new_state)
            except Exception as exc:
                self.request.response.setStatus(400)
                return {"error": f"Invalid state: {exc}"}

            old_state = conv.engine.get_page_state()
            if new_page_state != old_state:
                state_changed_externally = True
                conv.engine.replace_state(new_page_state)
                conv.original_volto = new_state
                conv.original_layout = copy.deepcopy(new_page_state.layout)

        # Build user message with optional prefix notes
        prefixes: list[str] = []

        if conv.read_only and (conv.first_message or state_changed_externally):
            state_json = json.dumps(
                conv.engine.get_page_state().model_dump(),
                ensure_ascii=False,
            )
            prefixes.append(f"[Aktueller Seiteninhalt:\n{state_json}]")
            for link, ref_eng in conv.reference_engines.items():
                ref_json = json.dumps(
                    ref_eng.get_page_state().model_dump(),
                    ensure_ascii=False,
                )
                prefixes.append(f"[Referenzseite ({link}):\n{ref_json}]")
        elif state_changed_externally:
            prefixes.append(
                "[Hinweis: Das Layout wurde in der Zwischenzeit vom Benutzer "
                "außerhalb dieses Chats geändert. Verwende get_layout, um den "
                "aktuellen Stand zu sehen, bevor du Änderungen vornimmst.]"
            )

        ctx = body.get("context")
        if ctx is not None:
            note = _build_context_note(ctx, conv)
            if note:
                prefixes.append(note)

        if prefixes:
            user_message = "\n\n".join([*prefixes, message])
        else:
            user_message = message

        # Create job and dispatch agent run
        job_id = str(uuid.uuid4())
        job = Job(job_id=job_id, conversation_id=conversation_id)
        conv.active_job = job
        jobs.create(job)

        loop = _get_loop()

        async def _wrapped():
            """Wrap _run_agent so we can store the asyncio.Task for cancellation."""
            job._async_task = asyncio.current_task()  # type: ignore[attr-defined]
            await _run_agent(job, conv, user_message)

        asyncio.run_coroutine_threadsafe(_wrapped(), loop)

        self.request.response.setStatus(201)
        return {"job_id": job_id}


# ---------------------------------------------------------------------------
# GET @ai-edit-job — poll job status (with live preview)
# ---------------------------------------------------------------------------


class AIEditPollJob(_EngineServiceBase):

    def reply(self) -> dict[str, Any]:
        job_id = self.request.get("job_id", "")
        if not job_id:
            self.request.response.setStatus(400)
            return {"error": "job_id query parameter is required"}

        job = jobs.get(job_id)
        if job is None:
            self.request.response.setStatus(404)
            return {"error": "Job not found."}

        if job.status == "running":
            result: dict[str, Any] = {"status": "running"}
            if job.progress:
                result["progress"] = job.progress
            # Live preview: include partial state
            conv = conversations.get(job.conversation_id)
            if conv is not None:
                current = conv.engine.get_page_state()
                if current.layout.root:
                    result["state"] = layout_to_volto(
                        current.layout,
                        current.metadata,
                        original_volto=conv.original_volto,
                        original_layout=conv.original_layout,
                    )
            return result

        if job.status == "completed":
            result = {"status": "completed", "message": job.message or ""}
            if job.state is not None:
                result["state"] = job.state
            return result

        if job.status == "failed":
            return {"status": "failed", "error": job.error or "Unknown error."}

        if job.status == "cancelled":
            return {"status": "cancelled"}

        return {"status": job.status}


# ---------------------------------------------------------------------------
# POST @ai-edit-cancel — cancel running job
# ---------------------------------------------------------------------------


class AIEditCancelJob(_EngineServiceBase):

    def reply(self) -> dict[str, Any]:
        body = self._read_body()
        job_id = body.get("job_id", "")
        if not job_id:
            self.request.response.setStatus(400)
            return {"error": "job_id is required"}

        job = jobs.get(job_id)
        if job is None:
            self.request.response.setStatus(404)
            return {"error": "Job not found."}

        if job.status != "running":
            self.request.response.setStatus(409)
            return {"error": "Job is already finished."}

        # Cancel the asyncio task running on the daemon-thread loop
        async_task = getattr(job, "_async_task", None)
        if async_task is not None and not async_task.done():
            loop = _get_loop()
            loop.call_soon_threadsafe(async_task.cancel)
        job.status = "cancelled"

        return {"status": "cancelled"}
