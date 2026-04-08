from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ImageAlignment(StrEnum):
    CENTER = "center"
    LEFT = "left"
    RIGHT = "right"
    FULL = "full"


class ImageSize(StrEnum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


class ButtonAlignment(StrEnum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


class SortOrder(StrEnum):
    ASCENDING = "ascending"
    DESCENDING = "descending"


class ListingDisplayVariant(StrEnum):
    STANDARD = "standard"
    SUMMARY_LIST = "summary_list"
    NEWS_LIST = "news_list"
    TWO_COLUMN_GRID = "two_column_grid"
    TEXT_CARD_GRID = "text_card_grid"
    VISUAL_CARD_GRID = "visual_card_grid"
    EVENT_LIST = "event_list"
    HORIZONTAL_LIST = "horizontal_list"


class QuoteDisplayVariant(StrEnum):
    STANDARD = "standard"
    TESTIMONIAL = "testimonial"


class TabsDisplayVariant(StrEnum):
    STANDARD = "standard"
    ACCORDION = "accordion"
    RESPONSIVE_TABS = "responsive_tabs"
    HORIZONTAL_CAROUSEL = "horizontal_carousel"
    VERTICAL_CAROUSEL = "vertical_carousel"


class HighlightBackgroundColor(StrEnum):
    LIGHT_BLUE = "light_blue"
    DARK_TEAL = "dark_teal"
    YELLOW = "yellow"
    LIGHT_GREEN = "light_green"
    OLIVE = "olive"


class SliderAutoplayTransition(StrEnum):
    SLIDE = "slide"
    JUMP = "jump"


class AccordionArrowPosition(StrEnum):
    LEFT = "left"
    RIGHT = "right"


class FieldVisibilityOperator(StrEnum):
    FILLED = "filled"
    EMPTY = "empty"
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"


type VisualAlignment = Literal["default", "left", "center", "right"]


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
    content_width: str = "default"


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
    alignment: ButtonAlignment
    open_link_in_new_tab: bool


class TeaserAttributes(BaseModel):
    link: str
    use_custom_content: bool
    title: str
    eyebrow: str
    description: str
    preview_image: str
    show_button: bool = False
    button_label: str = ""
    alignment: VisualAlignment = "default"
    button_style: str = "default"


class HighlightAttributes(BaseModel):
    image_url: str
    title: str
    html: str
    show_button: bool = True
    button_label: str
    button_link: str
    background_color: HighlightBackgroundColor | None = None


class TableAttributes(BaseModel):
    html: str
    minimal_style: bool
    show_cell_borders: bool
    compact: bool
    fixed_column_width: bool
    hide_headers: bool
    dark_background: bool
    striped_rows: bool


class PathFilter(BaseModel):
    """Restrict listing results to one or more subtrees."""

    type: Literal["path"]
    paths: list[str] = Field(min_length=1)


class ContentTypeFilter(BaseModel):
    """Filter listing results by content type."""

    type: Literal["content_type"]
    content_types: list[str] = Field(min_length=1)


class SubjectFilter(BaseModel):
    """Filter listing results by tags/subjects."""

    type: Literal["subject"]
    subjects: list[str] = Field(min_length=1)
    operator: Literal["any", "all"] = "any"


class DateFilter(BaseModel):
    """Filter listing results by a date field."""

    type: Literal["date"]
    field: str
    after: datetime | None = None
    before: datetime | None = None

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
    """Faceted content query for a listing block."""

    filters: list[QueryFilter] = Field(default_factory=list)
    sort_on: str = ""
    sort_order: SortOrder = SortOrder.ASCENDING
    limit: int = 10


class ListingItemAttributes(BaseModel):
    """Resolved listing result. Read-only for the agent."""

    content_path: str
    title: str
    description: str = ""
    content_type: str = ""
    preview_image: str = ""
    published: datetime | None = None


class ListingAttributes(BaseModel):
    heading: str
    heading_level: Annotated[int, Field(ge=2, le=3)]
    query: ListingQuery
    display_variant: ListingDisplayVariant = ListingDisplayVariant.STANDARD


class SlideAttributes(BaseModel):
    link: str
    eyebrow: str
    title: str
    description: str
    preview_image: str


class SliderAttributes(BaseModel):
    autoplay: bool
    autoplay_delay_ms: int
    autoplay_transition: SliderAutoplayTransition
    show_arrows: bool


class CarouselItemAttributes(BaseModel):
    link: str
    title: str
    description: str
    preview_image: str


class CarouselAttributes(BaseModel):
    heading: str
    visible_items: int
    show_descriptions: bool


class ColumnAttributes(BaseModel):
    width: Annotated[int, Field(ge=1, le=3)]


class ColumnsAttributes(BaseModel):
    reverse_stack_order: bool


class AccordionPanelAttributes(BaseModel):
    title: str


class AccordionAttributes(BaseModel):
    heading: str
    title: str
    arrow_position: AccordionArrowPosition = AccordionArrowPosition.RIGHT
    single_panel_open: bool
    start_collapsed: bool
    show_filter: bool
    heading_alignment: VisualAlignment = "default"
    heading_level: Annotated[int, Field(ge=2, le=3)] = 2
    content_width: str = "default"


class FieldVisibilityRule(BaseModel):
    """Show this field only when another field satisfies a rule."""

    field_id: str
    operator: FieldVisibilityOperator
    expected_value: str | None = None

    @model_validator(mode="after")
    def expected_value_required_for_value_checks(self) -> FieldVisibilityRule:
        needs_value = {
            FieldVisibilityOperator.EQUALS,
            FieldVisibilityOperator.NOT_EQUALS,
            FieldVisibilityOperator.CONTAINS,
            FieldVisibilityOperator.NOT_CONTAINS,
        }
        if self.operator in needs_value and not self.expected_value:
            raise ValueError(f"{self.operator.value!r} needs 'expected_value'.")
        if self.operator in (
            FieldVisibilityOperator.FILLED,
            FieldVisibilityOperator.EMPTY,
        ):
            self.expected_value = None
        return self


class QuoteAttributes(BaseModel):
    html: str
    attribution_html: str = ""
    context_html: str = ""
    display_variant: QuoteDisplayVariant = QuoteDisplayVariant.STANDARD
    alignment: VisualAlignment = "default"
    attribution_first: bool = False
    role_html: str = ""
    image_url: str = ""


class StatisticItemAttributes(BaseModel):
    value: str
    label: str
    info: str = ""
    link: str = ""
    prefix: str = ""
    suffix: str = ""


class StatisticAttributes(BaseModel):
    horizontal_layout: bool = False
    dark_background: bool = False
    size: str = "small"
    items_per_row: int = 1
    animation_enabled: bool = False
    animation_duration: float = 5.0
    animation_decimals: int = 0


class FormFieldAttributes(BaseModel):
    label: str
    description: str = ""
    required: bool = False
    input_type: Literal["text", "textarea", "number", "email", "date", "attachment"]
    use_as_reply_to: bool = False
    show_when: list[FieldVisibilityRule] = Field(default_factory=list)


class FormChoiceAttributes(BaseModel):
    label: str
    description: str = ""
    required: bool = False
    input_type: Literal["select", "radio", "checkbox"]
    options: list[str] = Field(default_factory=list)
    default: str = ""
    show_when: list[FieldVisibilityRule] = Field(default_factory=list)


class FormAttributes(BaseModel):
    title: str
    description: str = ""
    submit_button_label: str = "Submit"
    show_cancel_button: bool = False
    cancel_button_label: str = ""
    recipient_address: str
    email_subject: str = ""
    heading_alignment: VisualAlignment = "default"
    hidden_fields: dict[str, str] = Field(default_factory=dict)


class TabAttributes(BaseModel):
    title: str


class TabsAttributes(BaseModel):
    title: str = ""
    description: str = ""
    display_variant: TabsDisplayVariant = TabsDisplayVariant.STANDARD
    show_empty_tabs: bool = True


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


class QuoteBlock(BaseModel):
    type: Literal["quote"]
    id: str
    path: str
    name: str
    attributes: QuoteAttributes


# ---------------------------------------------------------------------------
# Child block models (only valid as children of specific container types)
# ---------------------------------------------------------------------------


class StatisticItemBlock(BaseModel):
    type: Literal["statistic_item"]
    id: str
    path: str
    name: str
    attributes: StatisticItemAttributes


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


class ListingItemBlock(BaseModel):
    type: Literal["listing_item"]
    id: str
    path: str
    name: str
    attributes: ListingItemAttributes


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


class ListingBlock(BaseModel):
    type: Literal["listing"]
    id: str
    path: str
    name: str
    attributes: ListingAttributes
    children: list[ListingItemBlock] = Field(default_factory=list)


class TabBlock(BaseModel):
    type: Literal["tab"]
    id: str
    path: str
    name: str
    attributes: TabAttributes
    children: list[Block]


class StatisticBlock(BaseModel):
    type: Literal["statistic"]
    id: str
    path: str
    name: str
    attributes: StatisticAttributes
    children: list[StatisticItemBlock]


class FormFieldBlock(BaseModel):
    """Generic form field block (covers all form_*_field types)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    type: str
    id: str
    path: str
    name: str
    attributes: Any


FormChild = FormFieldBlock | RichTextBlock


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


class Layout(RootModel[list[Block]]):
    """A full page layout is a list of top-level blocks."""


# Resolve forward references now that Block is defined.
ColumnBlock.model_rebuild()
AccordionPanelBlock.model_rebuild()
TabBlock.model_rebuild()
FormBlock.model_rebuild()


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
    start: datetime | None = None
    end: datetime | None = None


class PageState(BaseModel):
    """Full page IR: metadata + block layout."""

    metadata: Metadata
    layout: Layout
