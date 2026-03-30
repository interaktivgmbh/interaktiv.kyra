"""LangChain tools for the layout engine."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field, create_model

from interaktiv.kyra.agent.volto_vanilla import blocks
from interaktiv.kyra.agent.volto_vanilla.engine import Engine, EngineResult, MetadataPatchAttributes

ContainerPath = Annotated[
    str,
    Field(
        description=(
            "Absolute container scope path. Use '/' for top-level scope. "
            "Do not include the target element name; pass it in 'name'. "
            "Example nested scope: '/columns_1/column_1'."
        ),
    ),
]
ElementName = Annotated[
    str,
    Field(
        description="Element name in snake_case, unique within the target container."
    ),
]
AfterRef = Annotated[
    str | None,
    Field(default=None, description="Insert directly after this sibling name."),
]
BeforeRef = Annotated[
    str | None,
    Field(default=None, description="Insert directly before this sibling name."),
]
ToStartFlag = Annotated[
    bool,
    Field(
        default=False, description="If true, insert at the beginning of the container."
    ),
]
AfterNameRef = Annotated[
    str | None,
    Field(
        default=None, description="Insert after this sibling in destination container."
    ),
]
BeforeNameRef = Annotated[
    str | None,
    Field(
        default=None, description="Insert before this sibling in destination container."
    ),
]
NewNameRef = Annotated[
    str | None,
    Field(default=None, description="Optional new name in destination container."),
]
DestPath = Annotated[
    str,
    Field(
        description=(
            "Destination container scope path. Use '/' for top-level scope. "
            "Example: '/columns_1/column_2'."
        ),
    ),
]


def _result_to_str(result: EngineResult) -> str:
    return str(result.model_dump())


def _dump_create(attrs: blocks.CreateAttributes) -> dict[str, Any]:
    return attrs.model_dump(mode="python")


def _dump_patch(attrs: blocks.PatchAttributes) -> dict[str, Any]:
    return attrs.model_dump(mode="python", exclude_unset=True)


CanCopyReasoning = Annotated[
    str,
    Field(
        description=(
            "Prüfe, ob ein bestehendes Element kopiert werden könnte, "
            "statt ein neues zu erstellen. Gibt es auf der aktuellen Seite, "
            "einer Referenzseite oder einer Geschwisterseite ein Element, "
            "das als Vorlage dienen könnte?"
        ),
    ),
]
CanCopy = Annotated[
    bool,
    Field(
        description=(
            "true, wenn ein bestehendes Element kopiert und angepasst werden könnte. "
            "false, wenn kein passendes Element zum Kopieren existiert."
        ),
    ),
]


def _create_args_schema(spec: blocks.BlockSpec) -> type[BaseModel]:
    return create_model(
        f"{spec.create_model.__name__}ToolInput",
        can_copy_reasoning=(CanCopyReasoning, ...),
        can_copy=(CanCopy, ...),
        path=(ContainerPath, ...),
        name=(ElementName, ...),
        attributes=(spec.create_model, ...),
        after=(AfterRef, None),
        before=(BeforeRef, None),
        to_start=(ToStartFlag, False),
    )


def _update_args_schema(spec: blocks.BlockSpec) -> type[BaseModel]:
    return create_model(
        f"{spec.patch_model.__name__}ToolInput",
        path=(ContainerPath, ...),
        name=(ElementName, ...),
        attributes=(spec.patch_model, ...),
    )


def _make_create_tool(engine: Engine, spec: blocks.BlockSpec) -> BaseTool:
    args_schema = _create_args_schema(spec)

    def run_tool(**kwargs: Any) -> str:
        if kwargs.get("can_copy"):
            return _result_to_str(
                EngineResult(
                    ok=False,
                    code="copy_preferred",
                    message=(
                        "Erstellen abgelehnt: Es gibt ein bestehendes Element, "
                        "das kopiert werden könnte. Verwende copy_element, um es "
                        "zu kopieren und anschließend anzupassen."
                    ),
                )
            )
        attributes = kwargs["attributes"]
        return _result_to_str(
            engine.create_element(
                type=spec.type_name,
                path=kwargs["path"],
                name=kwargs["name"],
                attributes=_dump_create(attributes),
                after=kwargs.get("after"),
                before=kwargs.get("before"),
                to_start=kwargs.get("to_start", False),
            )
        )

    return tool(
        f"create_{spec.type_name}",
        description=spec.create_description,
        args_schema=args_schema,
    )(run_tool)


def _make_update_tool(engine: Engine, spec: blocks.BlockSpec) -> BaseTool:
    args_schema = _update_args_schema(spec)

    def run_tool(**kwargs: Any) -> str:
        attributes = kwargs["attributes"]
        return _result_to_str(
            engine.update_element(
                path=kwargs["path"],
                name=kwargs["name"],
                attributes=_dump_patch(attributes),
            )
        )

    return tool(
        f"update_{spec.type_name}",
        description=spec.update_description,
        args_schema=args_schema,
    )(run_tool)


def _resolve_engine(
    page: str | None,
    engine: Engine,
    reference_engines: dict[str, Engine] | None,
) -> Engine | EngineResult:
    """Resolve the target engine from the optional page parameter."""
    if page is None:
        return engine
    if reference_engines and page in reference_engines:
        return reference_engines[page]
    available = ", ".join(reference_engines) if reference_engines else "(none)"
    return EngineResult(
        ok=False,
        code="page_not_found",
        message=f"Reference page '{page}' not found. Available: {available}.",
    )


PageRef = Annotated[
    str | None,
    Field(
        default=None,
        description=(
            "Link of a reference page to read instead of the working page. "
            "Omit or null for the working page."
        ),
    ),
]


def make_read_tools(
    engine: Engine,
    reference_engines: dict[str, Engine] | None = None,
) -> list[BaseTool]:
    """get_layout."""

    @tool
    def get_layout(
        path: Annotated[
            str | None,
            Field(default=None, description="Container scope path. Null means root."),
        ] = None,
        name: Annotated[
            str | None,
            Field(default=None, description="Element name filter within scope."),
        ] = None,
        start: Annotated[
            int | None,
            Field(default=None, ge=0, description="Zero-based inclusive start index."),
        ] = None,
        end: Annotated[
            int | None,
            Field(default=None, ge=0, description="Zero-based exclusive end index."),
        ] = None,
        page: PageRef = None,
    ) -> str:
        """Read current layout in IR form, optionally scoped to a container and windowed. Use the page parameter to read a reference page."""
        target = _resolve_engine(page, engine, reference_engines)
        if isinstance(target, EngineResult):
            return _result_to_str(target)
        return _result_to_str(
            target.get_layout(path=path, name=name, start=start, end=end)
        )

    return [get_layout]


def make_metadata_read_tools(
    engine: Engine,
    reference_engines: dict[str, Engine] | None = None,
) -> list[BaseTool]:
    """get_metadata."""

    @tool
    def get_metadata(page: PageRef = None) -> str:
        """Read a page's metadata (title, description, preview image, tags, link). Use the page parameter to read a reference page."""
        target = _resolve_engine(page, engine, reference_engines)
        if isinstance(target, EngineResult):
            return _result_to_str(target)
        return _result_to_str(target.get_metadata())

    return [get_metadata]


def make_metadata_update_tools(engine: Engine) -> list[BaseTool]:
    """update_metadata."""

    @tool
    def update_metadata(attributes: MetadataPatchAttributes) -> str:
        """Update page metadata. Only provided fields are changed. Changing the title also updates the title block on the page."""
        return _result_to_str(
            engine.update_metadata(attributes=_dump_patch(attributes))
        )

    return [update_metadata]


DidBackupReasoning = Annotated[
    str,
    Field(
        description=(
            "Erkläre vor dem Löschen, ob du den Inhalt gesichert hast. "
            "Zum Beispiel: an anderer Stelle kopiert, bereits in einem neuen "
            "Block nachgebaut, oder der Nutzer hat ausdrücklich darum gebeten, "
            "den Inhalt endgültig zu entfernen."
        ),
    ),
]
DidBackup = Annotated[
    bool,
    Field(
        description=(
            "true, wenn der Inhalt gesichert, bereits nachgebaut oder "
            "vom Nutzer ausdrücklich zum endgültigen Löschen freigegeben wurde. "
            "false, wenn Informationen verloren gehen würden."
        ),
    ),
]


def make_delete_tools(engine: Engine) -> list[BaseTool]:
    """delete_element."""

    @tool
    def delete_element(
        did_backup_reasoning: DidBackupReasoning,
        did_backup: DidBackup,
        path: ContainerPath,
        name: ElementName,
    ) -> str:
        """Element aus einem Container löschen. Erfordert Bestätigung, dass der Inhalt gesichert oder bewusst verworfen wird."""
        if not did_backup:
            return _result_to_str(
                EngineResult(
                    ok=False,
                    code="backup_required",
                    message=(
                        "Löschen verweigert: Inhalt wurde nicht gesichert. "
                        "Kopiere oder erstelle den Inhalt zuerst neu, oder "
                        "bestätige, dass der Nutzer ihn endgültig entfernen möchte."
                    ),
                )
            )
        return _result_to_str(engine.delete_element(path=path, name=name))

    return [delete_element]


def make_move_tools(engine: Engine) -> list[BaseTool]:
    """move_element, swap_elements."""

    @tool
    def swap_elements(
        path_a: ContainerPath,
        name_a: ElementName,
        path_b: ContainerPath,
        name_b: ElementName,
    ) -> str:
        """Swap the positions of two elements across any containers."""
        return _result_to_str(
            engine.swap_elements(
                path_a=path_a,
                name_a=name_a,
                path_b=path_b,
                name_b=name_b,
            )
        )

    @tool
    def move_element(
        path: ContainerPath,
        name: ElementName,
        to_path: DestPath,
        after_name: AfterNameRef = None,
        before_name: BeforeNameRef = None,
        to_start: ToStartFlag = False,
        new_name: NewNameRef = None,
    ) -> str:
        """Move/reorder an element between container scopes. Defaults to append at destination."""
        return _result_to_str(
            engine.move_element(
                path=path,
                name=name,
                to_path=to_path,
                after_name=after_name,
                before_name=before_name,
                to_start=to_start,
                new_name=new_name,
            )
        )

    return [swap_elements, move_element]


def make_create_tools(engine: Engine) -> list[BaseTool]:
    """One create tool per block type."""

    tools: list[BaseTool] = [
        _make_create_tool(engine, spec) for spec in blocks.BLOCK_SPECS
    ]

    @tool
    def copy_element(
        path: ContainerPath,
        name: ElementName,
        to_path: DestPath,
        after_name: AfterNameRef = None,
        before_name: BeforeNameRef = None,
        to_start: ToStartFlag = False,
        new_name: NewNameRef = None,
    ) -> str:
        """Copy an element subtree to another container scope. Defaults to append at destination."""
        return _result_to_str(
            engine.copy_element(
                path=path,
                name=name,
                to_path=to_path,
                after_name=after_name,
                before_name=before_name,
                to_start=to_start,
                new_name=new_name,
            )
        )

    tools.append(copy_element)
    return tools


def make_update_tools(engine: Engine) -> list[BaseTool]:
    """One update (patch) tool per block type."""

    return [_make_update_tool(engine, spec) for spec in blocks.BLOCK_SPECS]


type Permission = Literal["create", "update", "delete", "move"]

ToolFactory = Callable[[Engine], list[BaseTool]]

_PERMISSION_MAP: dict[Permission, ToolFactory] = {
    "create": make_create_tools,
    "update": make_update_tools,
    "delete": make_delete_tools,
    "move": make_move_tools,
}


def make_tools(
    engine: Engine,
    permissions: list[Permission] | None = None,
    reference_engines: dict[str, Engine] | None = None,
) -> list[BaseTool]:
    """Build tools for the given Engine filtered by permissions."""
    tools = make_read_tools(engine, reference_engines=reference_engines)
    tools.extend(make_metadata_read_tools(engine, reference_engines=reference_engines))

    effective = permissions or []
    for perm in effective:
        factory = _PERMISSION_MAP.get(perm)
        if factory is None:
            raise ValueError(
                f"Unknown permission {perm!r}. Valid: {list(_PERMISSION_MAP)}"
            )
        tools.extend(factory(engine))

    if "update" in effective:
        tools.extend(make_metadata_update_tools(engine))

    return tools
