"""In-memory conversation and job storage for the integrated Layout Agent."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Any, Literal

from interaktiv.kyra.agent.volto_vanilla.engine import Engine
from interaktiv.kyra.agent.volto_vanilla.schema import Layout


@dataclass
class Conversation:
    conversation_id: str
    schema_name: str
    version: str
    language: str
    read_only: bool
    engine: Engine
    original_volto: dict[str, Any]
    original_layout: Layout
    agent: Any  # CompiledStateGraph
    config: dict[str, Any]
    reference_engines: dict[str, Engine] = field(default_factory=dict)
    first_message: bool = True
    active_job: Job | None = field(default=None, repr=False)


@dataclass
class Job:
    job_id: str
    conversation_id: str
    status: Literal["running", "completed", "failed", "cancelled"] = "running"
    progress: str | None = None
    message: str | None = None
    state: dict[str, Any] | None = None
    error: str | None = None
    task: asyncio.Task[None] | None = field(default=None, repr=False)


class ConversationStore:
    def __init__(self) -> None:
        self._data: dict[str, Conversation] = {}
        self._lock = threading.Lock()

    def create(self, conv: Conversation) -> None:
        with self._lock:
            self._data[conv.conversation_id] = conv

    def get(self, conversation_id: str) -> Conversation | None:
        with self._lock:
            return self._data.get(conversation_id)


class JobStore:
    def __init__(self) -> None:
        self._data: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, job: Job) -> None:
        with self._lock:
            self._data[job.job_id] = job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._data.get(job_id)


# Module-level singletons — survive across requests
conversations = ConversationStore()
jobs = JobStore()
