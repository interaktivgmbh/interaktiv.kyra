from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, RootModel, model_validator

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ImageAlignment(StrEnum):
    CENTER = "center"
    LEFT = "left"
    RIGHT = "right"
    FULL = "full"


class ImageSize(StrEnum):
    S = "s"
    M = "m"
    L = "l"


class InnerAlignment(StrEnum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


# ---------------------------------------------------------------------------
# Attribute models
# ---------------------------------------------------------------------------


class TitleAttributes(BaseModel):
    text: str


class DescriptionAttributes(BaseModel):
    text: str


class HeadingAttributes(BaseModel):
    text: str
    level: Annotated[int, Field(ge=2, le=3)]


class RichTextAttributes(BaseModel):
    html: str


class ImageAttributes(BaseModel):
    image_url: str
    alt_text: str
    alignment: ImageAlignment
    size: ImageSize
    link: str
    open_link_in_new_tab: bool


class DividerAttributes(BaseModel):
    text: str


class VideoAttributes(BaseModel):
    url: str
    preview_image: str
    alignment: str


class ButtonAttributes(BaseModel):
    title: str
    link: str
    inner_alignment: InnerAlignment
    open_link_in_new_tab: bool


class TeaserAttributes(BaseModel):
    link: str
    overwrite: bool
    title: str
    head_title: str
    description: str
    preview_image: str


class HighlightAttributes(BaseModel):
    image_url: str
    title: str
    html: str
    button_show: bool
    button_text: str
    button_link: str
    description_color: str | None = None


class TableAttributes(BaseModel):
    html: str
    minimal_style: bool
    show_cell_borders: bool
    compact: bool
    fixed_column_width: bool
    hide_headers: bool
    inverted_colors: bool
    striped_rows: bool


class SlideAttributes(BaseModel):
    link: str
    head_title: str
    title: str
    description: str
    preview_image: str


class SliderAttributes(BaseModel):
    autoplay: bool
    autoplay_delay: int
    autoplay_jump: bool
    hide_arrows: bool


class CarouselItemAttributes(BaseModel):
    link: str
    title: str
    description: str
    preview_image: str


class CarouselAttributes(BaseModel):
    headline: str
    visible_items: int
    hide_description: bool


class ColumnAttributes(BaseModel):
    width: Annotated[int, Field(ge=1, le=3)]


class ColumnsAttributes(BaseModel):
    reverse_wrap: bool


class AccordionPanelAttributes(BaseModel):
    title: str


class AccordionAttributes(BaseModel):
    headline: str
    title: str
    right_arrows: bool
    exclusive: bool
    collapsed: bool
    filtering: bool


# ---------------------------------------------------------------------------
# Leaf block models
# ---------------------------------------------------------------------------


class TitleBlock(BaseModel):
    type: Literal["title"]
    id: str
    path: str
    name: str
    attributes: TitleAttributes


class DescriptionBlock(BaseModel):
    type: Literal["description"]
    id: str
    path: str
    name: str
    attributes: DescriptionAttributes


class HeadingBlock(BaseModel):
    type: Literal["heading"]
    id: str
    path: str
    name: str
    attributes: HeadingAttributes


class RichTextBlock(BaseModel):
    type: Literal["rich_text"]
    id: str
    path: str
    name: str
    attributes: RichTextAttributes


class ImageBlock(BaseModel):
    type: Literal["image"]
    id: str
    path: str
    name: str
    attributes: ImageAttributes


class DividerBlock(BaseModel):
    type: Literal["divider"]
    id: str
    path: str
    name: str
    attributes: DividerAttributes


class VideoBlock(BaseModel):
    type: Literal["video"]
    id: str
    path: str
    name: str
    attributes: VideoAttributes


class ButtonBlock(BaseModel):
    type: Literal["button"]
    id: str
    path: str
    name: str
    attributes: ButtonAttributes


class TeaserBlock(BaseModel):
    type: Literal["teaser"]
    id: str
    path: str
    name: str
    attributes: TeaserAttributes


class HighlightBlock(BaseModel):
    type: Literal["highlight"]
    id: str
    path: str
    name: str
    attributes: HighlightAttributes


class TableBlock(BaseModel):
    type: Literal["table"]
    id: str
    path: str
    name: str
    attributes: TableAttributes


# ---------------------------------------------------------------------------
# Child block models (only valid as children of specific container types)
# ---------------------------------------------------------------------------


class SlideBlock(BaseModel):
    type: Literal["slide"]
    id: str
    path: str
    name: str
    attributes: SlideAttributes


class CarouselItemBlock(BaseModel):
    type: Literal["carousel_item"]
    id: str
    path: str
    name: str
    attributes: CarouselItemAttributes


class ColumnBlock(BaseModel):
    type: Literal["column"]
    id: str
    path: str
    name: str
    attributes: ColumnAttributes
    children: list[Block]


class AccordionPanelBlock(BaseModel):
    type: Literal["accordion_panel"]
    id: str
    path: str
    name: str
    attributes: AccordionPanelAttributes
    children: list[Block]


# ---------------------------------------------------------------------------
# Container block models
# ---------------------------------------------------------------------------


class SliderBlock(BaseModel):
    type: Literal["slider"]
    id: str
    path: str
    name: str
    attributes: SliderAttributes
    children: list[SlideBlock]


class CarouselBlock(BaseModel):
    type: Literal["carousel"]
    id: str
    path: str
    name: str
    attributes: CarouselAttributes
    children: list[CarouselItemBlock]


class ColumnsBlock(BaseModel):
    type: Literal["columns"]
    id: str
    path: str
    name: str
    attributes: ColumnsAttributes
    children: list[ColumnBlock]

    @model_validator(mode="after")
    def validate_total_width(self) -> ColumnsBlock:
        total = sum(col.attributes.width for col in self.children)
        if not 1 <= total <= 4:
            msg = f"Total column width must be between 1 and 4, got {total}"
            raise ValueError(msg)
        return self


class AccordionBlock(BaseModel):
    type: Literal["accordion"]
    id: str
    path: str
    name: str
    attributes: AccordionAttributes
    children: list[AccordionPanelBlock]


# ---------------------------------------------------------------------------
# Top-level block union
# ---------------------------------------------------------------------------

type Block = Annotated[
    TitleBlock
    | DescriptionBlock
    | HeadingBlock
    | RichTextBlock
    | ImageBlock
    | DividerBlock
    | VideoBlock
    | ButtonBlock
    | TeaserBlock
    | HighlightBlock
    | TableBlock
    | SliderBlock
    | CarouselBlock
    | ColumnsBlock
    | AccordionBlock,
    Field(discriminator="type"),
]


class Layout(RootModel[list[Block]]):
    """A full page layout is a list of top-level blocks."""


# Resolve forward references now that Block is defined.
ColumnBlock.model_rebuild()
AccordionPanelBlock.model_rebuild()


# ---------------------------------------------------------------------------
# Page metadata & top-level state
# ---------------------------------------------------------------------------


class Metadata(BaseModel):
    """Page-level metadata fields (independent of block content)."""

    link: str = ""
    title: str = ""
    description: str = ""
    preview_image: str = ""
    subjects: list[str] = Field(default_factory=list)


class PageState(BaseModel):
    """Full page IR: metadata + block layout."""

    metadata: Metadata
    layout: Layout
