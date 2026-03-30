"""Layout engine mutation API over the volto/vanilla IR."""

from __future__ import annotations

import copy
import uuid
from typing import Any, Protocol, TypeVar, cast

from pydantic import BaseModel, ValidationError

from interaktiv.kyra.agent.volto_vanilla import blocks as block_defs
from interaktiv.kyra.agent.volto_vanilla.blocks import MetadataPatchAttributes
from interaktiv.kyra.agent.volto_vanilla.schema import (
    DescriptionAttributes,
    Layout,
    Metadata,
    PageState,
    TitleAttributes,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


class BlockNode(Protocol):
    id: str
    path: str
    name: str
    type: str
    attributes: BaseModel

    def model_copy(self, *, deep: bool = False) -> BlockNode: ...

    def model_dump(self) -> dict[str, Any]: ...


BlockContainer = list[BlockNode]


class WidthAttributesNode(Protocol):
    width: int


class ColumnNode(Protocol):
    name: str
    attributes: WidthAttributesNode


class EngineResult(BaseModel):
    """Standard result envelope returned by every Engine method."""

    ok: bool
    code: str
    message: str
    data: Any = None


def _err(code: str, message: str, **data: Any) -> EngineResult:
    return EngineResult(ok=False, code=code, message=message, data=data or None)


def _ok(code: str, message: str, data: Any = None) -> EngineResult:
    return EngineResult(ok=True, code=code, message=message, data=data)


def _names(container: BlockContainer) -> list[str]:
    return [b.name for b in container]


def _find_block(container: BlockContainer, name: str) -> tuple[int, BlockNode] | None:
    for i, block in enumerate(container):
        if block.name == name:
            return i, block
    return None


def _resolve_position(
    container: BlockContainer,
    after: str | None,
    before: str | None,
    to_start: bool,
) -> int | EngineResult:
    """Determine insertion index. Returns int on success, EngineResult on error."""
    specs: list[tuple[str, int]] = []

    if to_start:
        specs.append(("to_start", 0))
    if after is not None:
        found = _find_block(container, after)
        if found is None:
            return _err(
                "position_not_found",
                f"Position reference '{after}' not found. Available: {', '.join(_names(container))}.",
            )
        specs.append(("after", found[0] + 1))
    if before is not None:
        found = _find_block(container, before)
        if found is None:
            return _err(
                "position_not_found",
                f"Position reference '{before}' not found. Available: {', '.join(_names(container))}.",
            )
        specs.append(("before", found[0]))

    if not specs:
        return len(container)

    indices = {idx for _, idx in specs}
    if len(indices) > 1:
        detail = ", ".join(f"{label}={idx}" for label, idx in specs)
        return _err(
            "conflicting_position",
            f"Position arguments resolve to different indices: {detail}.",
        )

    return specs[0][1]


def _update_paths(block: BlockNode, new_path: str) -> None:
    """Recursively update path on a block and its descendants."""
    block.path = new_path
    if hasattr(block, "children"):
        child_path = new_path.rstrip("/") + "/" + block.name
        for child in cast(BlockContainer, getattr(block, "children")):
            _update_paths(child, child_path)


def _deep_copy(block: BlockNode) -> BlockNode:
    """Deep-copy a block subtree, assigning fresh UUIDs."""
    clone = block.model_copy(deep=True)
    _assign_fresh_ids(clone)
    return clone


def _assign_fresh_ids(block: BlockNode) -> None:
    """Recursively assign new UUIDs to a block and its children."""
    block.id = str(uuid.uuid4())
    if hasattr(block, "children"):
        for child in cast(BlockContainer, getattr(block, "children")):
            _assign_fresh_ids(child)


class Engine:
    """Stateful layout engine operating on the IR."""

    def __init__(self, page_state: PageState) -> None:
        self._metadata = page_state.metadata
        self._layout = page_state.layout

    @classmethod
    def from_page_state(cls, page_state: PageState) -> Engine:
        """Create an engine from an existing PageState."""
        return cls(page_state)

    @property
    def metadata(self) -> Metadata:
        """Read-only access to current metadata (no copy)."""
        return self._metadata

    def replace_state(self, page_state: PageState) -> None:
        """Swap the internal state. Existing tool references stay valid."""
        self._metadata = page_state.metadata
        self._layout = page_state.layout

    def _block_spec(self, block_type: str) -> block_defs.BlockSpec | None:
        return block_defs.BLOCK_SPECS_BY_TYPE.get(block_type)

    def _validate_model(
        self,
        model_cls: type[ModelT],
        attributes: dict[str, Any],
        *,
        code: str,
        message_prefix: str,
    ) -> ModelT | EngineResult:
        try:
            return model_cls(**attributes)
        except ValidationError as exc:
            msgs = "; ".join(e["msg"] for e in exc.errors())
            return _err(code, f"{message_prefix}: {msgs}.")

    def _sync_metadata_from_block_update(
        self,
        block_type: str,
        merged_attributes: dict[str, Any],
        updated_fields: dict[str, Any],
    ) -> None:
        if block_type == "title" and "text" in updated_fields:
            self._metadata.title = merged_attributes["text"]
        if block_type == "description" and "text" in updated_fields:
            self._metadata.description = merged_attributes["text"]

    def _resolve_html_patches(
        self,
        patch: BaseModel,
        current_attrs: BaseModel,
    ) -> dict[str, Any] | EngineResult:
        """Turn a validated patch into a plain-dict update.

        ``HtmlPatch`` values are applied as substring replacements against the
        corresponding field in *current_attrs*; all other values pass through
        unchanged.
        """
        updates = patch.model_dump(exclude_none=True)
        for field_name in list(updates):
            value = getattr(patch, field_name)
            if isinstance(value, block_defs.HtmlPatch):
                current_val = getattr(current_attrs, field_name, "")
                if not isinstance(current_val, str):
                    current_val = ""
                if value.old not in current_val:
                    return _err(
                        "html_patch_not_found",
                        f"Substring not found in '{field_name}'. "
                        f"Use get_layout to read the current content first.",
                    )
                updates[field_name] = current_val.replace(value.old, value.new)
        return updates

    def _build_block(
        self,
        spec: block_defs.BlockSpec,
        *,
        path: str,
        name: str,
        validated_attributes: BaseModel,
    ) -> BlockNode:
        attrs_obj = spec.attributes_model(**validated_attributes.model_dump())
        block_data: dict[str, Any] = {
            "type": spec.type_name,
            "id": str(uuid.uuid4()),
            "path": path,
            "name": name,
            "attributes": attrs_obj,
        }
        if spec.type_name in block_defs.CONTAINER_TYPES:
            block_data["children"] = []
        return cast(BlockNode, spec.block_model.model_construct(**block_data))

    def _resolve_container(self, path: str) -> BlockContainer | EngineResult:
        """Resolve a path to its container's children list."""
        if path == "/":
            return cast(BlockContainer, self._layout.root)

        segments = path.strip("/").split("/")
        current: BlockContainer = cast(BlockContainer, self._layout.root)

        for i, segment in enumerate(segments):
            found = _find_block(current, segment)
            if found is None:
                resolved_so_far = "/" + "/".join(segments[:i]) if i > 0 else "/"
                return _err(
                    "container_not_found",
                    f"Container segment '{segment}' not found at {resolved_so_far}. "
                    f"Available: {', '.join(_names(current))}.",
                )
            _, block = found
            if not hasattr(block, "children"):
                return _err(
                    "not_a_container",
                    f"'{segment}' is a {block.type} block and cannot contain children.",
                )
            current = cast(BlockContainer, getattr(block, "children"))

        return current

    def _find_container_block(
        self, path: str
    ) -> tuple[BlockNode, BlockContainer] | EngineResult:
        """Find the container block itself (not its children) for a given path."""
        if path == "/":
            return _err("invalid_path", "Root has no container block.")

        segments = path.strip("/").split("/")
        current: BlockContainer = cast(BlockContainer, self._layout.root)

        for i, segment in enumerate(segments):
            found = _find_block(current, segment)
            if found is None:
                resolved_so_far = "/" + "/".join(segments[:i]) if i > 0 else "/"
                return _err(
                    "container_not_found",
                    f"Container segment '{segment}' not found at {resolved_so_far}. "
                    f"Available: {', '.join(_names(current))}.",
                )
            _, block = found
            if i == len(segments) - 1:
                return block, current
            if not hasattr(block, "children"):
                return _err(
                    "not_a_container",
                    f"'{segment}' is a {block.type} block and cannot contain children.",
                )
            current = cast(BlockContainer, getattr(block, "children"))

        return _err("invalid_path", f"Could not resolve path '{path}'.")

    def _validate_parent_child(self, path: str, child_type: str) -> EngineResult | None:
        """Check that child_type is allowed at path. Returns error or None."""
        if path == "/":
            if child_type in block_defs.CHILD_ONLY_TYPES:
                return _err(
                    "invalid_parent_child",
                    f"'{child_type}' cannot be placed at root. "
                    f"It must be inside a {block_defs.PARENT_NEEDED[child_type]}.",
                )
            return None

        result = self._find_container_block(path)
        if isinstance(result, EngineResult):
            return result
        container_block, _ = result
        container_type = container_block.type

        if container_type in block_defs.CHILD_TYPES:
            allowed = block_defs.CHILD_TYPES[container_type]
            if child_type != allowed:
                return _err(
                    "invalid_parent_child",
                    f"Cannot place '{child_type}' inside {container_type}. "
                    f"Only accepts: {allowed}.",
                )
            return None

        if container_type in block_defs.RESTRICTED_CHILD_TYPES:
            allowed_set = block_defs.RESTRICTED_CHILD_TYPES[container_type]
            if child_type not in allowed_set:
                return _err(
                    "invalid_parent_child",
                    f"Cannot place '{child_type}' inside {container_type}. "
                    f"Only accepts: [{', '.join(sorted(allowed_set))}].",
                )
            return None

        if container_type in block_defs.OPEN_CONTAINER_TYPES:
            if child_type in block_defs.CHILD_ONLY_TYPES:
                return _err(
                    "invalid_parent_child",
                    f"'{child_type}' cannot be placed inside {container_type}. "
                    f"It must be inside a {block_defs.PARENT_NEEDED[child_type]}.",
                )
            return None

        return _err(
            "not_a_container",
            f"'{container_type}' cannot contain children.",
        )

    def _check_column_width(self, path: str) -> EngineResult | None:
        """After modifying a column, validate total width of its parent columns block."""
        result = self._find_container_block(path)
        if isinstance(result, EngineResult):
            return None
        container_block, _ = result
        if container_block.type != "columns":
            return None
        columns = cast(list[ColumnNode], getattr(container_block, "children"))
        total = sum(col.attributes.width for col in columns)
        if not 1 <= total <= 4:
            widths = ", ".join(f"{col.name}={col.attributes.width}" for col in columns)
            return _err(
                "invalid_column_width",
                f"Column width sum is {total} (must be 1–4). Current widths: {widths}.",
            )
        return None

    def find_block_by_id(self, block_id: str) -> tuple[str, str, str] | None:
        """Find a block by its Volto UUID."""
        return self._search_by_id(cast(BlockContainer, self._layout.root), block_id)

    def _search_by_id(
        self, blocks: BlockContainer, block_id: str
    ) -> tuple[str, str, str] | None:
        for block in blocks:
            if block.id == block_id:
                return (block.path, block.name, block.type)
            if hasattr(block, "children"):
                result = self._search_by_id(
                    cast(BlockContainer, getattr(block, "children")), block_id
                )
                if result is not None:
                    return result
        return None

    def get_layout(
        self,
        *,
        path: str | None = None,
        name: str | None = None,
        start: int | None = None,
        end: int | None = None,
    ) -> EngineResult:
        """Read current layout (optionally scoped/windowed)."""
        container = self._resolve_container(path or "/")
        if isinstance(container, EngineResult):
            return container

        if name is not None:
            found = _find_block(container, name)
            if found is None:
                return _err(
                    "element_not_found",
                    f"Element '{name}' not found at {path or '/'}. "
                    f"Available: {', '.join(_names(container))}.",
                )
            _, block = found
            return _ok(
                "layout", f"Element '{name}' at {path or '/'}.", data=block.model_dump()
            )

        blocks = (
            container[start or 0 : end]
            if start is not None or end is not None
            else container
        )
        return _ok(
            "layout",
            f"{len(blocks)} element(s) at {path or '/'}.",
            data=[b.model_dump() for b in blocks],
        )

    def create_element(
        self,
        *,
        type: str,
        path: str,
        name: str,
        attributes: dict[str, Any],
        after: str | None = None,
        before: str | None = None,
        to_start: bool = False,
    ) -> EngineResult:
        """Create a new element of the given type in the target container."""
        spec = self._block_spec(type)
        if spec is None:
            return _err(
                "unknown_type",
                f"Unknown block type '{type}'. Known types: {', '.join(sorted(block_defs.BLOCK_SPECS_BY_TYPE))}.",
            )

        container = self._resolve_container(path)
        if isinstance(container, EngineResult):
            return container

        pc_err = self._validate_parent_child(path, type)
        if pc_err is not None:
            return pc_err

        if _find_block(container, name) is not None:
            return _err(
                "name_exists",
                f"Name '{name}' already exists at {path}. Choose a different name.",
            )

        validated = self._validate_model(
            spec.create_model,
            attributes,
            code="invalid_attributes",
            message_prefix=f"Invalid attributes for {type}",
        )
        if isinstance(validated, EngineResult):
            return validated

        pos = _resolve_position(container, after, before, to_start)
        if isinstance(pos, EngineResult):
            return pos

        block = self._build_block(
            spec,
            path=path,
            name=name,
            validated_attributes=validated,
        )
        container.insert(pos, block)

        if type == "column":
            inv_err = self._check_column_width(path)
            if inv_err is not None:
                container.remove(block)
                return inv_err

        self._sync_metadata_from_block_update(
            type, validated.model_dump(), {"text": True}
        )
        return _ok(
            "created",
            f"Created {type} '{name}' at {path}.",
            data=block.model_dump(),
        )

    def update_element(
        self,
        *,
        path: str,
        name: str,
        attributes: dict[str, Any],
    ) -> EngineResult:
        """Patch attributes on an existing element."""
        container = self._resolve_container(path)
        if isinstance(container, EngineResult):
            return container

        found = _find_block(container, name)
        if found is None:
            return _err(
                "element_not_found",
                f"Element '{name}' not found at {path}. "
                f"Available: {', '.join(_names(container))}.",
            )
        _, block = found
        block_type = block.type

        spec = self._block_spec(block_type)
        if spec is None:
            return _err("unknown_type", f"Unknown block type '{block_type}'.")

        patch = self._validate_model(
            spec.patch_model,
            attributes,
            code="invalid_attributes",
            message_prefix=f"Invalid attributes for {block_type}",
        )
        if isinstance(patch, EngineResult):
            return patch

        # Resolve HtmlPatch fields (substring replacement) before merging.
        resolved = self._resolve_html_patches(patch, block.attributes)
        if isinstance(resolved, EngineResult):
            return resolved

        merged_attributes = block.attributes.model_dump()
        merged_attributes.update(resolved)
        new_attrs = self._validate_model(
            spec.attributes_model,
            merged_attributes,
            code="invalid_attributes",
            message_prefix=f"Merged attributes invalid for {block_type}",
        )
        if isinstance(new_attrs, EngineResult):
            return new_attrs

        old_attrs = block.attributes
        block.attributes = new_attrs

        if block_type == "column":
            inv_err = self._check_column_width(path)
            if inv_err is not None:
                block.attributes = old_attrs
                return inv_err

        self._sync_metadata_from_block_update(block_type, merged_attributes, attributes)
        return _ok(
            "updated",
            f"Updated '{name}' at {path}.",
            data=block.model_dump(),
        )

    def delete_element(
        self,
        *,
        path: str,
        name: str,
    ) -> EngineResult:
        """Delete an element from a container scope."""
        container = self._resolve_container(path)
        if isinstance(container, EngineResult):
            return container

        found = _find_block(container, name)
        if found is None:
            return _err(
                "element_not_found",
                f"Element '{name}' not found at {path}. "
                f"Available: {', '.join(_names(container))}.",
            )
        idx, block = found
        container.pop(idx)

        if block.type == "column":
            inv_err = self._check_column_width(path)
            if inv_err is not None:
                container.insert(idx, block)
                return inv_err

        return _ok("deleted", f"Deleted '{name}' from {path}.")

    def swap_elements(
        self,
        *,
        path_a: str,
        name_a: str,
        path_b: str,
        name_b: str,
    ) -> EngineResult:
        """Swap the positions of two elements across any containers."""
        cont_a = self._resolve_container(path_a)
        if isinstance(cont_a, EngineResult):
            return cont_a
        found_a = _find_block(cont_a, name_a)
        if found_a is None:
            return _err(
                "element_not_found",
                f"Element '{name_a}' not found at {path_a}. "
                f"Available: {', '.join(_names(cont_a))}.",
            )

        cont_b = self._resolve_container(path_b)
        if isinstance(cont_b, EngineResult):
            return cont_b
        found_b = _find_block(cont_b, name_b)
        if found_b is None:
            return _err(
                "element_not_found",
                f"Element '{name_b}' not found at {path_b}. "
                f"Available: {', '.join(_names(cont_b))}.",
            )

        idx_a, block_a = found_a
        idx_b, block_b = found_b

        # Validate parent-child compatibility in the swapped positions.
        pc_err = self._validate_parent_child(path_a, block_b.type)
        if pc_err is not None:
            return pc_err
        pc_err = self._validate_parent_child(path_b, block_a.type)
        if pc_err is not None:
            return pc_err

        # Perform the swap.
        cont_a[idx_a] = block_b
        cont_b[idx_b] = block_a
        _update_paths(block_a, path_b)
        _update_paths(block_b, path_a)

        # Validate column width constraints if columns are involved.
        if block_a.type == "column" or block_b.type == "column":
            for check_path in {path_a, path_b}:
                width_err = self._check_column_width(check_path)
                if width_err is not None:
                    # Rollback.
                    cont_a[idx_a] = block_a
                    cont_b[idx_b] = block_b
                    _update_paths(block_a, path_a)
                    _update_paths(block_b, path_b)
                    return width_err

        return _ok(
            "swapped",
            f"Swapped '{name_a}' ({path_a}) and '{name_b}' ({path_b}).",
        )

    def move_element(
        self,
        *,
        path: str,
        name: str,
        to_path: str,
        after_name: str | None = None,
        before_name: str | None = None,
        to_start: bool = False,
        new_name: str | None = None,
    ) -> EngineResult:
        """Move/reorder an element between container scopes."""
        src_container = self._resolve_container(path)
        if isinstance(src_container, EngineResult):
            return src_container

        found = _find_block(src_container, name)
        if found is None:
            return _err(
                "element_not_found",
                f"Element '{name}' not found at {path}. "
                f"Available: {', '.join(_names(src_container))}.",
            )
        src_idx, block = found

        block_abs = (path.rstrip("/") + "/" + name) if path != "/" else ("/" + name)
        if to_path == block_abs or to_path.startswith(block_abs + "/"):
            return _err(
                "cycle_detected",
                f"Cannot move '{name}' into its own subtree ({to_path}).",
            )

        dst_container = self._resolve_container(to_path)
        if isinstance(dst_container, EngineResult):
            return dst_container

        same_container = dst_container is src_container

        pc_err = self._validate_parent_child(to_path, block.type)
        if pc_err is not None:
            return pc_err

        final_name = new_name or name
        existing = _find_block(dst_container, final_name)
        if existing is not None and not (same_container and final_name == name):
            return _err(
                "name_exists",
                f"Name '{final_name}' already exists at {to_path}. Choose a different name.",
            )

        pos = _resolve_position(dst_container, after_name, before_name, to_start)
        if isinstance(pos, EngineResult):
            return pos

        src_container.pop(src_idx)
        if same_container and src_idx < pos:
            pos -= 1

        if new_name:
            block.name = new_name
        _update_paths(block, to_path)
        dst_container.insert(pos, block)

        if block.type == "column":
            inv_err = self._check_column_width(to_path)
            if inv_err is not None:
                dst_container.remove(block)
                block.name = name
                _update_paths(block, path)
                src_container.insert(src_idx, block)
                return inv_err

        return _ok(
            "moved",
            f"Moved '{name}' from {path} to {to_path}"
            + (f" as '{new_name}'." if new_name else "."),
        )

    def copy_element(
        self,
        *,
        path: str,
        name: str,
        to_path: str,
        after_name: str | None = None,
        before_name: str | None = None,
        to_start: bool = False,
        new_name: str | None = None,
    ) -> EngineResult:
        """Copy an element subtree to another container scope."""
        src_container = self._resolve_container(path)
        if isinstance(src_container, EngineResult):
            return src_container

        found = _find_block(src_container, name)
        if found is None:
            return _err(
                "element_not_found",
                f"Element '{name}' not found at {path}. "
                f"Available: {', '.join(_names(src_container))}.",
            )
        _, block = found

        dst_container = self._resolve_container(to_path)
        if isinstance(dst_container, EngineResult):
            return dst_container

        pc_err = self._validate_parent_child(to_path, block.type)
        if pc_err is not None:
            return pc_err

        final_name = new_name or name
        if _find_block(dst_container, final_name) is not None:
            return _err(
                "name_exists",
                f"Name '{final_name}' already exists at {to_path}. Choose a different name.",
            )

        clone = _deep_copy(block)
        clone.name = final_name
        _update_paths(clone, to_path)

        pos = _resolve_position(dst_container, after_name, before_name, to_start)
        if isinstance(pos, EngineResult):
            return pos

        dst_container.insert(pos, clone)
        if clone.type == "column":
            inv_err = self._check_column_width(to_path)
            if inv_err is not None:
                dst_container.remove(clone)
                return inv_err

        return _ok(
            "copied",
            f"Copied '{name}' to {to_path} as '{final_name}'.",
            data=clone.model_dump(),
        )

    def get_metadata(self) -> EngineResult:
        """Read current page metadata."""
        return _ok(
            "metadata", "Current page metadata.", data=self._metadata.model_dump()
        )

    def update_metadata(self, *, attributes: dict[str, Any]) -> EngineResult:
        """Patch page metadata fields. Only provided fields are updated."""
        patch = self._validate_model(
            MetadataPatchAttributes,
            attributes,
            code="invalid_attributes",
            message_prefix="Invalid metadata attributes",
        )
        if isinstance(patch, EngineResult):
            return patch

        updates = {
            k: v
            for k, v in patch.model_dump(exclude_unset=True).items()
            if v is not None
        }
        if not updates:
            return _err("no_fields", "No metadata fields provided to update.")

        for key, value in updates.items():
            setattr(self._metadata, key, value)

        if "title" in updates:
            self._sync_title_to_blocks(self._metadata.title)
        if "description" in updates:
            self._sync_description_to_blocks(self._metadata.description)

        return _ok(
            "metadata_updated",
            f"Updated metadata field(s): {', '.join(updates)}.",
            data=self._metadata.model_dump(),
        )

    def _sync_title_to_blocks(self, new_title: str) -> None:
        self._sync_blocks_by_type(
            cast(BlockContainer, self._layout.root),
            "title",
            TitleAttributes(text=new_title),
        )

    def _sync_description_to_blocks(self, new_description: str) -> None:
        self._sync_blocks_by_type(
            cast(BlockContainer, self._layout.root),
            "description",
            DescriptionAttributes(text=new_description),
        )

    def _sync_blocks_by_type(
        self, blocks: BlockContainer, block_type: str, attrs: BaseModel
    ) -> None:
        for block in blocks:
            if block.type == block_type:
                block.attributes = attrs
            if hasattr(block, "children"):
                self._sync_blocks_by_type(
                    cast(BlockContainer, getattr(block, "children")),
                    block_type,
                    attrs,
                )

    def get_page_state(self) -> PageState:
        """Return a deep copy of the current page state."""
        return PageState(
            metadata=copy.deepcopy(self._metadata),
            layout=copy.deepcopy(self._layout),
        )

    def get_state(self) -> EngineResult:
        """Export current page state as serialisable IR."""
        return _ok(
            "state", "Current page state.", data=self.get_page_state().model_dump()
        )
