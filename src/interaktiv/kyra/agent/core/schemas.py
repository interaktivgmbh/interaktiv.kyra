"""CMS-agnostic schemas: content tree, block IR, page state, and Site protocol.

This module is the canonical definition of the layout IR. It has no CMS-specific
imports. A CMS adapter implements the Site protocol.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from abc import ABC, abstractmethod
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, RootModel, model_validator

# ===================================================================
# Content tree
# ===================================================================


class ContentNode(BaseModel):
    """Summary of a content object in the site tree."""

    path: str
    title: str
    description: str = ""
    content_type: str
    has_children: bool = False
    subjects: list[str] = Field(default_factory=list)
    preview_image: str = ""
    created: datetime | None = None
    modified: datetime | None = None
    published: datetime | None = None
    start: datetime | None = None
    end: datetime | None = None


# ===================================================================
# Result type
# ===================================================================


class Result(BaseModel):
    """Returned by all layout operations on the Site."""

    ok: bool
    message: str
    data: Any = None


class DocumentChunk(BaseModel):
    """A text chunk from a document stored in the CMS."""

    source_path: str
    """Content path of the source document, e.g. '/rathaus/formulare/bauantrag'."""

    source_title: str
    """Title of the source document."""

    text: str
    """The chunk text."""

    page: int | None = None
    """Page number in the source document, if applicable."""


# ===================================================================
# Block IR — enums
# ===================================================================


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


class SortOrder(StrEnum):
    ASCENDING = "ascending"
    DESCENDING = "descending"


# ===================================================================
# Block IR — attribute models
# ===================================================================


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
    alignment: ImageAlignment


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


HighlightColor = Literal["light-blue", "dark-teal", "olive", "yellow", "light-green"]

HIGHLIGHT_COLORS: list[str] = list(HighlightColor.__args__)  # type: ignore[attr-defined]


class HighlightAttributes(BaseModel):
    image_url: str
    title: str
    html: str
    button_show: bool = True
    button_text: str
    button_link: str
    description_color: HighlightColor | None = None


class TableAttributes(BaseModel):
    html: str
    minimal_style: bool
    show_cell_borders: bool
    compact: bool
    fixed_column_width: bool
    hide_headers: bool
    inverted_colors: bool
    striped_rows: bool


# --- Listing ---


class PathFilter(BaseModel):
    """Restrict results to one or more subtrees."""

    type: Literal["path"] = Field(description='Must be "path".')
    paths: list[str] = Field(
        min_length=1, description="Content paths to scope to, e.g. ['/news']."
    )


class ContentTypeFilter(BaseModel):
    """Filter by content type."""

    type: Literal["content_type"] = Field(description='Must be "content_type".')
    content_types: list[str] = Field(
        min_length=1,
        description="Match any of these types, e.g. ['Document', 'News Item'].",
    )


class SubjectFilter(BaseModel):
    """Filter by tags/subjects."""

    type: Literal["subject"] = Field(description='Must be "subject".')
    subjects: list[str] = Field(min_length=1, description="Tags to filter by.")
    operator: Literal["any", "all"] = Field(
        default="any",
        description="'any' = match at least one tag (OR), 'all' = match every tag (AND).",
    )


class DateFilter(BaseModel):
    """Filter by a date field."""

    type: Literal["date"] = Field(description='Must be "date".')
    field: str = Field(
        description="Which date to filter on, e.g. 'created', 'modified', 'published'."
    )
    after: datetime | None = Field(
        default=None, description="Only include items after this date."
    )
    before: datetime | None = Field(
        default=None, description="Only include items before this date."
    )

    @model_validator(mode="after")
    def at_least_one_bound(self) -> DateFilter:
        if self.after is None and self.before is None:
            raise ValueError("DateFilter needs at least one of 'after' or 'before'.")
        return self


type QueryFilter = Annotated[
    PathFilter | ContentTypeFilter | SubjectFilter | DateFilter,
    Field(discriminator="type"),
]


class ListingQuery(BaseModel):
    """Faceted content query — determines what a listing block displays."""

    filters: list[QueryFilter] = Field(default_factory=list)
    sort_on: str = ""
    sort_order: SortOrder = SortOrder.ASCENDING
    limit: int = 10


class ListingItemAttributes(BaseModel):
    """Resolved listing result. Populated by the CMS adapter — read-only for the agent."""

    content_path: str
    title: str
    description: str = ""
    content_type: str = ""
    preview_image: str = ""
    published: datetime | None = None


class ListingAttributes(BaseModel):
    headline: str
    headline_level: Annotated[int, Field(ge=2, le=3)]
    query: ListingQuery
    variation: str


# --- Slider ---


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


# --- Carousel ---


class CarouselItemAttributes(BaseModel):
    link: str
    title: str
    description: str
    preview_image: str


class CarouselAttributes(BaseModel):
    headline: str
    visible_items: int
    hide_description: bool


# --- Columns ---


class ColumnAttributes(BaseModel):
    width: Annotated[int, Field(ge=1, le=3)]


class ColumnsAttributes(BaseModel):
    reverse_wrap: bool


# --- Accordion ---


class AccordionPanelAttributes(BaseModel):
    title: str


class AccordionAttributes(BaseModel):
    headline: str
    title: str
    right_arrows: bool
    exclusive: bool
    collapsed: bool
    filtering: bool


# --- Quote ---


class QuoteAttributes(BaseModel):
    html: str
    source_html: str = ""
    extra_html: str = ""
    variation: Literal["default", "testimonial"] = "default"
    position: Literal["left", "center", "right"] | None = None
    reversed: bool = False
    title_html: str = ""
    image_url: str = ""


# --- Statistic ---


class StatisticItemAttributes(BaseModel):
    value: str
    label: str
    info: str = ""
    link: str = ""
    prefix: str = ""
    suffix: str = ""


class StatisticAttributes(BaseModel):
    horizontal: bool = True
    inverted: bool = False
    size: Literal["mini", "tiny", "small", "large", "huge"] = "small"
    widths: Annotated[int, Field(ge=1, le=4)] = 1
    animation_enabled: bool = False
    animation_duration: float = 5.0
    animation_decimals: int = 0


# --- Form ---


class FormFieldAttributes(BaseModel):
    label: str
    description: str = ""
    required: bool = False
    kind: Literal["text", "textarea", "number", "email", "date", "attachment"]
    send_copy: bool = False


class FormChoiceAttributes(BaseModel):
    label: str
    description: str = ""
    required: bool = False
    kind: Literal["select", "radio", "checkbox"]
    options: list[str] = Field(default_factory=list)
    default: str = ""


class FormAttributes(BaseModel):
    title: str
    description: str = ""
    submit_label: str = "Submit"
    show_cancel: bool = False
    cancel_label: str = ""
    recipient_email: str
    subject: str = ""
    metadata: dict[str, str] = Field(default_factory=dict)


# --- Tabs ---


class TabAttributes(BaseModel):
    title: str


class TabsAttributes(BaseModel):
    title: str = ""
    description: str = ""
    variation: Literal[
        "default",
        "accordion",
        "horizontal-responsive",
        "carousel-horizontal",
        "carousel-vertical",
    ] = "default"
    hide_empty_tabs: bool = False


# ===================================================================
# Block IR — update schemas
# ===================================================================

_RICH_TEXT_HTML_DESCRIPTION = (
    "HTML content. Allowed tags: p, h2, h3, ul, ol, li, blockquote, "
    "a, br, strong, b, em, i, u, s, del, sub, sup. "
    "Only 'href' is allowed on <a>. "
    "Use <p> for paragraphs, <br> for line breaks within a paragraph. "
    "No style, class, or id attributes."
)


class TitleUpdate(BaseModel):
    text: str | None = Field(default=None, description="Page title text.")


class DescriptionUpdate(BaseModel):
    text: str | None = Field(
        default=None, description="Page description / summary text."
    )


class HeadingUpdate(BaseModel):
    text: str | None = Field(default=None, description="Heading text.")
    level: Literal[2, 3] | None = Field(
        default=None, description="Heading level: 2 (h2) or 3 (h3)."
    )


class RichTextUpdate(BaseModel):
    html: str | None = Field(default=None, description=_RICH_TEXT_HTML_DESCRIPTION)


class ImageUpdate(BaseModel):
    image_url: str | None = Field(default=None, description="Image URL or path.")
    alt_text: str | None = Field(
        default=None, description="Alt text for accessibility."
    )
    alignment: ImageAlignment | None = Field(
        default=None, description="Alignment: center, left, right, or full."
    )
    size: ImageSize | None = Field(
        default=None, description="Display size: s, m, or l."
    )
    link: str | None = Field(default=None, description="Click-through link URL.")
    open_link_in_new_tab: bool | None = Field(
        default=None, description="Open link in new tab."
    )


class DividerUpdate(BaseModel):
    text: str | None = Field(default=None, description="Label on the divider line.")


class VideoUpdate(BaseModel):
    url: str | None = Field(default=None, description="Video or embed URL.")
    preview_image: str | None = Field(default=None, description="Preview image URL.")
    alignment: ImageAlignment | None = Field(
        default=None, description="Alignment: center, left, right, or full."
    )


class ButtonUpdate(BaseModel):
    title: str | None = Field(default=None, description="Button label text.")
    link: str | None = Field(
        default=None, description="Button destination URL or path."
    )
    inner_alignment: InnerAlignment | None = Field(
        default=None, description="Button position: left, center, or right."
    )
    open_link_in_new_tab: bool | None = Field(
        default=None, description="Open link in new tab."
    )


class TeaserUpdate(BaseModel):
    link: str | None = Field(
        default=None, description="Destination URL or content path."
    )
    overwrite: bool | None = Field(
        default=None,
        description="Use custom title/description instead of linked page metadata.",
    )
    title: str | None = Field(default=None, description="Teaser title.")
    head_title: str | None = Field(
        default=None, description="Eyebrow line above the title."
    )
    description: str | None = Field(default=None, description="Teaser description.")
    preview_image: str | None = Field(default=None, description="Preview image URL.")


class HighlightUpdate(BaseModel):
    image_url: str | None = Field(default=None, description="Image URL.")
    title: str | None = Field(default=None, description="Highlight heading.")
    html: str | None = Field(
        default=None, description="Body HTML. " + _RICH_TEXT_HTML_DESCRIPTION
    )
    button_show: bool | None = Field(
        default=None,
        description="Whether the CTA button is visible. Defaults to true — set to false only if the highlight should have no button.",
    )
    button_text: str | None = Field(default=None, description="CTA button label.")
    button_link: str | None = Field(default=None, description="CTA button destination.")
    description_color: HighlightColor | None = Field(
        default=None,
        description="Background color for the description area. One of: light-blue, dark-teal, olive, yellow, light-green.",
    )


class TableUpdate(BaseModel):
    html: str | None = Field(
        default=None, description="Table HTML (table, thead, tbody, tr, th, td)."
    )
    minimal_style: bool | None = Field(
        default=None, description="Minimal visual style."
    )
    show_cell_borders: bool | None = Field(
        default=None, description="Show cell borders."
    )
    compact: bool | None = Field(default=None, description="Compact row spacing.")
    fixed_column_width: bool | None = Field(
        default=None, description="Equal-width columns."
    )
    hide_headers: bool | None = Field(default=None, description="Hide header row.")
    inverted_colors: bool | None = Field(default=None, description="Dark background.")
    striped_rows: bool | None = Field(
        default=None, description="Alternating row colors."
    )


class ListingUpdate(BaseModel):
    headline: str | None = Field(default=None, description="Heading above the listing.")
    headline_level: Literal[2, 3] | None = Field(
        default=None, description="Heading level: 2 or 3."
    )
    query: ListingQuery | None = Field(
        default=None, description="Replaces the entire content query."
    )
    variation: str | None = Field(
        default=None,
        description="Display template, e.g. 'default', 'summary', 'news_image'.",
    )


class SlideUpdate(BaseModel):
    link: str | None = Field(default=None, description="Destination URL when clicked.")
    head_title: str | None = Field(default=None, description="Eyebrow line.")
    title: str | None = Field(default=None, description="Slide title.")
    description: str | None = Field(default=None, description="Slide description.")
    preview_image: str | None = Field(default=None, description="Background image URL.")


class CarouselItemUpdate(BaseModel):
    link: str | None = Field(default=None, description="Destination URL when clicked.")
    title: str | None = Field(default=None, description="Item title.")
    description: str | None = Field(default=None, description="Item description.")
    preview_image: str | None = Field(default=None, description="Item image URL.")


class ColumnUpdate(BaseModel):
    width: Literal[1, 2, 3] | None = Field(
        default=None, description="Column width (1-3). Sum must be 1-4."
    )


class AccordionPanelUpdate(BaseModel):
    title: str | None = Field(default=None, description="Panel heading text.")


class SliderUpdate(BaseModel):
    autoplay: bool | None = Field(default=None, description="Auto-cycle slides.")
    autoplay_delay: int | None = Field(
        default=None, description="Delay in ms between slides."
    )
    autoplay_jump: bool | None = Field(
        default=None, description="Jump instead of animate."
    )
    hide_arrows: bool | None = Field(
        default=None, description="Hide navigation arrows."
    )


class CarouselUpdate(BaseModel):
    headline: str | None = Field(
        default=None, description="Heading above the carousel."
    )
    visible_items: int | None = Field(
        default=None, description="Items visible at once."
    )
    hide_description: bool | None = Field(
        default=None, description="Hide item descriptions."
    )


class ColumnsUpdate(BaseModel):
    reverse_wrap: bool | None = Field(
        default=None, description="Reverse column order on mobile."
    )


class AccordionUpdate(BaseModel):
    headline: str | None = Field(
        default=None, description="Heading above the accordion."
    )
    title: str | None = Field(default=None, description="Accordion title.")
    right_arrows: bool | None = Field(default=None, description="Arrows on the right.")
    exclusive: bool | None = Field(
        default=None, description="Only one panel open at a time."
    )
    collapsed: bool | None = Field(
        default=None, description="All panels start collapsed."
    )
    filtering: bool | None = Field(default=None, description="Show filter input.")


class QuoteUpdate(BaseModel):
    html: str | None = Field(
        default=None, description="Quote text HTML. " + _RICH_TEXT_HTML_DESCRIPTION
    )
    source_html: str | None = Field(
        default=None,
        description="Attribution / source HTML. " + _RICH_TEXT_HTML_DESCRIPTION,
    )
    extra_html: str | None = Field(
        default=None,
        description="Extra context HTML. " + _RICH_TEXT_HTML_DESCRIPTION,
    )
    variation: Literal["default", "testimonial"] | None = Field(
        default=None, description="Visual variation."
    )
    position: Literal["left", "center", "right"] | None = Field(
        default=None, description="Quote alignment."
    )
    reversed: bool | None = Field(
        default=None, description="Show source before quote text."
    )
    title_html: str | None = Field(
        default=None,
        description="Testimonial title HTML (e.g. person's role). "
        + _RICH_TEXT_HTML_DESCRIPTION,
    )
    image_url: str | None = Field(default=None, description="Testimonial image URL.")


class StatisticItemUpdate(BaseModel):
    value: str | None = Field(default=None, description="The number or metric.")
    label: str | None = Field(default=None, description="Label below the value.")
    info: str | None = Field(default=None, description="Extra info text.")
    link: str | None = Field(default=None, description="Link URL.")
    prefix: str | None = Field(default=None, description="Text before animated value.")
    suffix: str | None = Field(default=None, description="Text after animated value.")


class StatisticUpdate(BaseModel):
    horizontal: bool | None = Field(default=None, description="Horizontal layout.")
    inverted: bool | None = Field(default=None, description="Dark background.")
    size: Literal["mini", "tiny", "small", "large", "huge"] | None = Field(
        default=None, description="Display size."
    )
    widths: Annotated[int, Field(ge=1, le=4)] | None = Field(
        default=None, description="Number of columns (1-4)."
    )
    animation_enabled: bool | None = Field(
        default=None, description="Enable count-up animation."
    )
    animation_duration: float | None = Field(
        default=None, description="Animation duration in seconds."
    )
    animation_decimals: int | None = Field(
        default=None, description="Decimal places in animated number."
    )


class FormFieldUpdate(BaseModel):
    label: str | None = Field(default=None, description="Field label.")
    description: str | None = Field(default=None, description="Help text.")
    required: bool | None = Field(default=None, description="Required field.")
    kind: (
        Literal["text", "textarea", "number", "email", "date", "attachment"] | None
    ) = Field(default=None, description="Input type.")
    send_copy: bool | None = Field(
        default=None,
        description="Send a copy of the submission to this email address (only for email fields).",
    )


class FormChoiceUpdate(BaseModel):
    label: str | None = Field(default=None, description="Field label.")
    description: str | None = Field(default=None, description="Help text.")
    required: bool | None = Field(default=None, description="Required field.")
    kind: Literal["select", "radio", "checkbox"] | None = Field(
        default=None,
        description="Choice presentation: select (dropdown), radio, or checkbox.",
    )
    options: list[str] | None = Field(
        default=None, description="Choice options (replaces entire list)."
    )
    default: str | None = Field(default=None, description="Pre-selected option.")


class FormUpdate(BaseModel):
    title: str | None = Field(default=None, description="Form title.")
    description: str | None = Field(default=None, description="Form description.")
    submit_label: str | None = Field(default=None, description="Submit button label.")
    show_cancel: bool | None = Field(default=None, description="Show cancel button.")
    cancel_label: str | None = Field(default=None, description="Cancel button label.")
    recipient_email: str | None = Field(
        default=None, description="Recipient email address."
    )
    subject: str | None = Field(default=None, description="Email subject line.")
    metadata: dict[str, str] | None = Field(
        default=None,
        description="Hidden key-value pairs submitted with the form (replaces entire dict).",
    )


class TabUpdate(BaseModel):
    title: str | None = Field(default=None, description="Tab heading.")


class TabsUpdate(BaseModel):
    title: str | None = Field(default=None, description="Block title.")
    description: str | None = Field(default=None, description="Block description.")
    variation: (
        Literal[
            "default",
            "accordion",
            "horizontal-responsive",
            "carousel-horizontal",
            "carousel-vertical",
        ]
        | None
    ) = Field(default=None, description="Display variation.")
    hide_empty_tabs: bool | None = Field(
        default=None, description="Hide tabs with no content."
    )


class MetadataUpdate(BaseModel):
    title: str | None = Field(default=None, description="Page title.")
    description: str | None = Field(default=None, description="Page summary.")
    preview_image: str | None = Field(default=None, description="Preview image URL.")
    subjects: list[str] | None = Field(
        default=None, description="Tags (replaces entire list)."
    )
    start: datetime | None = Field(
        default=None, description="Event start date/time (ISO 8601)."
    )
    end: datetime | None = Field(
        default=None, description="Event end date/time (ISO 8601)."
    )


# ===================================================================
# Block type registry — maps type names to attribute + update models
# ===================================================================

BLOCK_TYPES: dict[str, tuple[type[BaseModel], type[BaseModel]]] = {
    "title": (TitleAttributes, TitleUpdate),
    "description": (DescriptionAttributes, DescriptionUpdate),
    "heading": (HeadingAttributes, HeadingUpdate),
    "rich_text": (RichTextAttributes, RichTextUpdate),
    "image": (ImageAttributes, ImageUpdate),
    "divider": (DividerAttributes, DividerUpdate),
    "video": (VideoAttributes, VideoUpdate),
    "button": (ButtonAttributes, ButtonUpdate),
    "teaser": (TeaserAttributes, TeaserUpdate),
    "highlight": (HighlightAttributes, HighlightUpdate),
    "table": (TableAttributes, TableUpdate),
    "listing": (ListingAttributes, ListingUpdate),
    "slide": (SlideAttributes, SlideUpdate),
    "carousel_item": (CarouselItemAttributes, CarouselItemUpdate),
    "column": (ColumnAttributes, ColumnUpdate),
    "accordion_panel": (AccordionPanelAttributes, AccordionPanelUpdate),
    "slider": (SliderAttributes, SliderUpdate),
    "carousel": (CarouselAttributes, CarouselUpdate),
    "columns": (ColumnsAttributes, ColumnsUpdate),
    "accordion": (AccordionAttributes, AccordionUpdate),
    "quote": (QuoteAttributes, QuoteUpdate),
    "statistic_item": (StatisticItemAttributes, StatisticItemUpdate),
    "statistic": (StatisticAttributes, StatisticUpdate),
    "form_field": (FormFieldAttributes, FormFieldUpdate),
    "form_choice": (FormChoiceAttributes, FormChoiceUpdate),
    "form": (FormAttributes, FormUpdate),
    "tab": (TabAttributes, TabUpdate),
    "tabs": (TabsAttributes, TabsUpdate),
}

# Types that should not get create/update tools (read-only children).
SKIP_TOOL_TYPES = {"listing_item"}


# ===================================================================
# Block IR — block models
# ===================================================================

# --- Leaf blocks ---


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


class QuoteBlock(BaseModel):
    type: Literal["quote"]
    id: str
    path: str
    name: str
    attributes: QuoteAttributes


# --- Container child blocks ---


class ListingItemBlock(BaseModel):
    type: Literal["listing_item"]
    id: str
    path: str
    name: str
    attributes: ListingItemAttributes


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


class StatisticItemBlock(BaseModel):
    type: Literal["statistic_item"]
    id: str
    path: str
    name: str
    attributes: StatisticItemAttributes


class FormFieldBlock(BaseModel):
    type: Literal["form_field"]
    id: str
    path: str
    name: str
    attributes: FormFieldAttributes


class FormChoiceBlock(BaseModel):
    type: Literal["form_choice"]
    id: str
    path: str
    name: str
    attributes: FormChoiceAttributes


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


class TabBlock(BaseModel):
    type: Literal["tab"]
    id: str
    path: str
    name: str
    attributes: TabAttributes
    children: list[Block]


# --- Container blocks ---


FormChild = Annotated[
    FormFieldBlock | FormChoiceBlock | RichTextBlock,
    Field(discriminator="type"),
]


class ListingBlock(BaseModel):
    type: Literal["listing"]
    id: str
    path: str
    name: str
    attributes: ListingAttributes
    children: list[ListingItemBlock] = Field(default_factory=list)


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
            raise ValueError(f"Total column width must be between 1 and 4, got {total}")
        return self


class AccordionBlock(BaseModel):
    type: Literal["accordion"]
    id: str
    path: str
    name: str
    attributes: AccordionAttributes
    children: list[AccordionPanelBlock]


class StatisticBlock(BaseModel):
    type: Literal["statistic"]
    id: str
    path: str
    name: str
    attributes: StatisticAttributes
    children: list[StatisticItemBlock]


class FormBlock(BaseModel):
    type: Literal["form"]
    id: str
    path: str
    name: str
    attributes: FormAttributes
    children: list[FormChild]


class TabsBlock(BaseModel):
    type: Literal["tabs"]
    id: str
    path: str
    name: str
    attributes: TabsAttributes
    children: list[TabBlock]


# ===================================================================
# Block union & layout
# ===================================================================

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
    | QuoteBlock
    | ListingBlock
    | SliderBlock
    | CarouselBlock
    | ColumnsBlock
    | AccordionBlock
    | StatisticBlock
    | FormBlock
    | TabsBlock,
    Field(discriminator="type"),
]

ColumnBlock.model_rebuild()
AccordionPanelBlock.model_rebuild()
TabBlock.model_rebuild()


class Layout(RootModel[list[Block]]):
    """A full page layout is a list of top-level blocks."""


BLOCK_MODELS: dict[str, type[BaseModel]] = {
    "title": TitleBlock,
    "description": DescriptionBlock,
    "heading": HeadingBlock,
    "rich_text": RichTextBlock,
    "image": ImageBlock,
    "divider": DividerBlock,
    "video": VideoBlock,
    "button": ButtonBlock,
    "teaser": TeaserBlock,
    "highlight": HighlightBlock,
    "table": TableBlock,
    "listing": ListingBlock,
    "listing_item": ListingItemBlock,
    "slide": SlideBlock,
    "carousel_item": CarouselItemBlock,
    "column": ColumnBlock,
    "accordion_panel": AccordionPanelBlock,
    "slider": SliderBlock,
    "carousel": CarouselBlock,
    "columns": ColumnsBlock,
    "accordion": AccordionBlock,
    "quote": QuoteBlock,
    "statistic_item": StatisticItemBlock,
    "statistic": StatisticBlock,
    "form_field": FormFieldBlock,
    "form_choice": FormChoiceBlock,
    "form": FormBlock,
    "tab": TabBlock,
    "tabs": TabsBlock,
}


# ===================================================================
# Page state
# ===================================================================


class Metadata(BaseModel):
    path: str = ""
    title: str = ""
    description: str = ""
    preview_image: str = ""
    subjects: list[str] = Field(default_factory=list)
    start: datetime | None = None
    end: datetime | None = None


class PageState(BaseModel):
    metadata: Metadata
    layout: Layout


# ===================================================================
# Site protocol
# ===================================================================


class Site(ABC):
    """Unified interface to a CMS: browse the content tree, read and
    mutate page layouts. Implemented by CMS adapters.

    All methods are async. The adapter handles authentication, validation,
    visibility filtering, and persistence.
    """

    # --- Current page ---

    @property
    @abstractmethod
    def current_page(self) -> str: ...

    @abstractmethod
    def set_current_page(self, page: str) -> None: ...

    # --- Content tree ---

    @abstractmethod
    async def get_node(self, path: str) -> ContentNode | None: ...

    @abstractmethod
    async def get_ancestors(self, path: str) -> list[ContentNode]: ...

    @abstractmethod
    async def get_children(
        self,
        path: str,
        *,
        content_type: str | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> list[ContentNode]: ...

    @abstractmethod
    async def search(
        self,
        *,
        query: str | None = None,
        path: str | None = None,
        content_type: str | None = None,
        subjects: list[str] | None = None,
        limit: int = 10,
    ) -> list[ContentNode]: ...

    # --- Documents ---

    @abstractmethod
    async def search_documents(
        self,
        query: str,
        *,
        path: str | None = None,
        limit: int = 5,
    ) -> list[DocumentChunk]: ...

    @abstractmethod
    async def read_document_pages(
        self,
        path: str,
        *,
        start_page: int = 1,
        end_page: int = 5,
    ) -> Result: ...

    # --- Images ---

    @abstractmethod
    async def resolve_image(
        self,
        path: str,
        *,
        scale: str = "large",
    ) -> str | None: ...

    # --- Page layout ---

    @abstractmethod
    async def get_layout(
        self,
        page: str,
        *,
        path: str = "/",
        name: str | None = None,
    ) -> Result: ...

    @abstractmethod
    async def get_metadata(self, page: str) -> Result: ...

    @abstractmethod
    async def create_element(
        self,
        page: str,
        *,
        block_type: str,
        path: str,
        name: str,
        attributes: dict[str, Any],
        after: str | None = None,
        before: str | None = None,
        to_start: bool = False,
    ) -> Result: ...

    @abstractmethod
    async def update_element(
        self,
        page: str,
        *,
        path: str,
        name: str,
        attributes: dict[str, Any],
    ) -> Result: ...

    @abstractmethod
    async def delete_element(
        self,
        page: str,
        *,
        path: str,
        name: str,
    ) -> Result: ...

    @abstractmethod
    async def swap_elements(
        self,
        page: str,
        *,
        path_a: str,
        name_a: str,
        path_b: str,
        name_b: str,
    ) -> Result: ...

    @abstractmethod
    async def move_element(
        self,
        page: str,
        *,
        path: str,
        name: str,
        to_path: str,
        after_name: str | None = None,
        before_name: str | None = None,
        to_start: bool = False,
        new_name: str | None = None,
    ) -> Result: ...

    @abstractmethod
    async def copy_element(
        self,
        page: str,
        *,
        source_page: str | None = None,
        path: str,
        name: str,
        to_path: str,
        after_name: str | None = None,
        before_name: str | None = None,
        to_start: bool = False,
        new_name: str | None = None,
    ) -> Result: ...

    @abstractmethod
    async def update_metadata(
        self,
        page: str,
        *,
        attributes: dict[str, Any],
    ) -> Result: ...
