"""Shared block operation metadata for the volto/vanilla engine and tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from interaktiv.kyra.agent.volto_vanilla.schema import (
    AccordionAttributes,
    AccordionBlock,
    AccordionPanelAttributes,
    AccordionPanelBlock,
    ButtonAttributes,
    ButtonBlock,
    CarouselAttributes,
    CarouselBlock,
    CarouselItemAttributes,
    CarouselItemBlock,
    ColumnAttributes,
    ColumnBlock,
    ColumnsAttributes,
    ColumnsBlock,
    DescriptionAttributes,
    DescriptionBlock,
    DividerAttributes,
    DividerBlock,
    FormAttributes,
    FormBlock,
    FormEmailFieldAttributes,
    FormFieldBlock,
    FormHiddenFieldAttributes,
    FormSelectFieldAttributes,
    FormTextFieldAttributes,
    HeadingAttributes,
    HeadingBlock,
    HighlightAttributes,
    HighlightBlock,
    ImageAlignment,
    ImageAttributes,
    ImageBlock,
    ImageSize,
    InnerAlignment,
    PdfViewerAttributes,
    PdfViewerBlock,
    QuoteAttributes,
    QuoteBlock,
    RichTextAttributes,
    RichTextBlock,
    SlideAttributes,
    SlideBlock,
    SliderAttributes,
    SliderBlock,
    StatisticAttributes,
    StatisticBlock,
    StatisticItemAttributes,
    StatisticItemBlock,
    TabAttributes,
    TabBlock,
    TableAttributes,
    TableBlock,
    TabsAttributes,
    TabsBlock,
    TeaserAttributes,
    TeaserBlock,
    TitleAttributes,
    TitleBlock,
    VideoAttributes,
    VideoBlock,
)


class CreateAttributes(BaseModel):
    """Base for create attribute models (all fields required)."""

    model_config = ConfigDict(extra="forbid")


class PatchAttributes(BaseModel):
    """Base for patch attribute models (all fields optional, exclude_unset)."""

    model_config = ConfigDict(extra="forbid")


class TitleCreateAttributes(CreateAttributes):
    """Title block — the page title."""

    text: str = Field(description="Page title text.")


class TitlePatchAttributes(PatchAttributes):
    """Patch attributes for the page title."""

    text: str | None = Field(default=None, description="Page title text.")


class DescriptionCreateAttributes(CreateAttributes):
    """Description block — the page description/summary."""

    text: str = Field(description="Page description text.")


class DescriptionPatchAttributes(PatchAttributes):
    """Patch attributes for the page description."""

    text: str | None = Field(default=None, description="Page description text.")


class HeadingCreateAttributes(CreateAttributes):
    """Section heading (h2 or h3)."""

    text: str = Field(description="Heading text content.")
    level: Literal[2, 3] = Field(description="Heading level (2 or 3).")


class HeadingPatchAttributes(PatchAttributes):
    """Patch attributes for heading blocks."""

    text: str | None = Field(default=None, description="Heading text content.")
    level: Literal[2, 3] | None = Field(
        default=None, description="Heading level (2 or 3)."
    )


class RichTextCreateAttributes(CreateAttributes):
    """Rich-text block with HTML content.

    Allowed tags: p, h2, h3, ul, ol, li, blockquote, a, br,
    strong, b, em, i, u, s, del, code. Only 'href' is allowed on <a>.
    Use <p> for paragraphs, <br> only for line breaks within a paragraph.
    No style/class/id attributes.
    """

    html: str = Field(
        description=(
            "Rich-text HTML. Allowed tags: p, h2, h3, ul, ol, li, blockquote, "
            "a, br, strong, b, em, i, u, s, del, code. Only 'href' on <a>. "
            "Use <p> for paragraphs, <br> for line breaks within a paragraph."
        ),
    )


class RichTextPatchAttributes(PatchAttributes):
    """Patch attributes for rich-text blocks."""

    html: str | None = Field(
        default=None,
        description=(
            "Rich-text HTML. Allowed tags: p, h2, h3, ul, ol, li, blockquote, "
            "a, br, strong, b, em, i, u, s, del, code. Only 'href' on <a>. "
            "Use <p> for paragraphs, <br> for line breaks within a paragraph."
        ),
    )


class ImageCreateAttributes(CreateAttributes):
    """Image block."""

    image_url: str = Field(description="Image URL or path.")
    alt_text: str = Field(default="", description="Accessibility alt text.")
    alignment: ImageAlignment = Field(description="Image alignment.")
    size: ImageSize = Field(description="Image display size.")
    link: str = Field(default="", description="Optional click-through link.")
    open_link_in_new_tab: bool = Field(
        description="Open image link in a new browser tab."
    )


class ImagePatchAttributes(PatchAttributes):
    """Patch attributes for image blocks."""

    image_url: str | None = Field(default=None, description="Image URL or path.")
    alt_text: str | None = Field(default=None, description="Accessibility alt text.")
    alignment: ImageAlignment | None = Field(
        default=None, description="Image alignment."
    )
    size: ImageSize | None = Field(default=None, description="Image display size.")
    link: str | None = Field(default=None, description="Optional click-through link.")
    open_link_in_new_tab: bool | None = Field(
        default=None, description="Open image link in a new browser tab."
    )


class DividerCreateAttributes(CreateAttributes):
    """Horizontal divider/separator."""

    text: str = Field(description="Optional divider label text.")


class DividerPatchAttributes(PatchAttributes):
    """Patch attributes for divider blocks."""

    text: str | None = Field(default=None, description="Optional divider label text.")


class VideoCreateAttributes(CreateAttributes):
    """Embedded video block."""

    url: str = Field(description="Video/embed URL.")
    preview_image: str = Field(description="Preview/thumbnail image URL.")
    alignment: str = Field(description="Video block alignment.")


class VideoPatchAttributes(PatchAttributes):
    """Patch attributes for video blocks."""

    url: str | None = Field(default=None, description="Video/embed URL.")
    preview_image: str | None = Field(
        default=None, description="Preview/thumbnail image URL."
    )
    alignment: str | None = Field(default=None, description="Video block alignment.")


class ButtonCreateAttributes(CreateAttributes):
    """Call-to-action button."""

    title: str = Field(description="Button label text.")
    link: str = Field(description="Button destination URL/path.")
    inner_alignment: InnerAlignment = Field(
        description="Button alignment within the block."
    )
    open_link_in_new_tab: bool = Field(description="Open link in a new browser tab.")


class ButtonPatchAttributes(PatchAttributes):
    """Patch attributes for button blocks."""

    title: str | None = Field(default=None, description="Button label text.")
    link: str | None = Field(default=None, description="Button destination URL/path.")
    inner_alignment: InnerAlignment | None = Field(
        default=None, description="Button alignment within the block."
    )
    open_link_in_new_tab: bool | None = Field(
        default=None, description="Open link in a new browser tab."
    )


class TeaserCreateAttributes(CreateAttributes):
    """Linked preview card / teaser."""

    link: str = Field(description="Destination URL/path.")
    overwrite: bool = Field(
        description="Whether teaser title/description override linked content metadata."
    )
    title: str = Field(description="Teaser title.")
    head_title: str = Field(description="Optional eyebrow/head title.")
    description: str = Field(description="Teaser description text.")
    preview_image: str = Field(description="Preview image URL.")


class TeaserPatchAttributes(PatchAttributes):
    """Patch attributes for teaser blocks."""

    link: str | None = Field(default=None, description="Destination URL/path.")
    overwrite: bool | None = Field(
        default=None,
        description="Whether teaser title/description override linked content metadata.",
    )
    title: str | None = Field(default=None, description="Teaser title.")
    head_title: str | None = Field(
        default=None, description="Optional eyebrow/head title."
    )
    description: str | None = Field(
        default=None, description="Teaser description text."
    )
    preview_image: str | None = Field(default=None, description="Preview image URL.")


class HighlightCreateAttributes(CreateAttributes):
    """Featured/highlight content block."""

    image_url: str = Field(description="Optional image URL.")
    title: str = Field(description="Highlight title.")
    html: str = Field(description="Highlight body HTML.")
    button_show: bool = Field(description="Whether the CTA button is visible.")
    button_text: str = Field(description="CTA button label.")
    button_link: str = Field(description="CTA button destination.")
    description_color: str | None = Field(
        default=None,
        description="Background color for the description area. One of: light-blue, dark-teal, olive, yellow, light-green.",
    )


class HighlightPatchAttributes(PatchAttributes):
    """Patch attributes for highlight blocks."""

    image_url: str | None = Field(default=None, description="Optional image URL.")
    title: str | None = Field(default=None, description="Highlight title.")
    html: str | None = Field(default=None, description="Highlight body HTML.")
    button_show: bool | None = Field(
        default=None, description="Whether the CTA button is visible."
    )
    button_text: str | None = Field(default=None, description="CTA button label.")
    button_link: str | None = Field(default=None, description="CTA button destination.")
    description_color: str | None = Field(
        default=None,
        description="Background color for the description area. One of: light-blue, dark-teal, olive, yellow, light-green.",
    )


class TableCreateAttributes(CreateAttributes):
    """Table block represented as HTML."""

    html: str = Field(description="Table HTML.")
    minimal_style: bool = Field(description="Minimal visual style.")
    show_cell_borders: bool = Field(description="Whether to show borders around cells.")
    compact: bool = Field(description="Whether table spacing is compact.")
    fixed_column_width: bool = Field(description="Whether columns use fixed widths.")
    hide_headers: bool = Field(description="Whether the header row is hidden.")
    inverted_colors: bool = Field(description="Whether the table uses inverted colors.")
    striped_rows: bool = Field(description="Whether rows are striped.")


class TablePatchAttributes(PatchAttributes):
    """Patch attributes for table blocks."""

    html: str | None = Field(default=None, description="Table HTML.")
    minimal_style: bool | None = Field(
        default=None, description="Minimal visual style."
    )
    show_cell_borders: bool | None = Field(
        default=None, description="Whether to show borders around cells."
    )
    compact: bool | None = Field(
        default=None, description="Whether table spacing is compact."
    )
    fixed_column_width: bool | None = Field(
        default=None, description="Whether columns use fixed widths."
    )
    hide_headers: bool | None = Field(
        default=None, description="Whether the header row is hidden."
    )
    inverted_colors: bool | None = Field(
        default=None, description="Whether the table uses inverted colors."
    )
    striped_rows: bool | None = Field(
        default=None, description="Whether rows are striped."
    )


class SlideCreateAttributes(CreateAttributes):
    """Slider slide."""

    link: str = Field(description="Optional destination URL/path.")
    head_title: str = Field(description="Eyebrow/head title.")
    title: str = Field(description="Slide title.")
    description: str = Field(description="Slide description.")
    preview_image: str = Field(description="Slide preview image URL.")


class SlidePatchAttributes(PatchAttributes):
    """Patch attributes for slide blocks."""

    link: str | None = Field(default=None, description="Optional destination URL/path.")
    head_title: str | None = Field(default=None, description="Eyebrow/head title.")
    title: str | None = Field(default=None, description="Slide title.")
    description: str | None = Field(default=None, description="Slide description.")
    preview_image: str | None = Field(
        default=None, description="Slide preview image URL."
    )


class CarouselItemCreateAttributes(CreateAttributes):
    """Carousel item."""

    link: str = Field(description="Optional destination URL/path.")
    title: str = Field(description="Item title.")
    description: str = Field(description="Item description.")
    preview_image: str = Field(description="Item preview image URL.")


class CarouselItemPatchAttributes(PatchAttributes):
    """Patch attributes for carousel items."""

    link: str | None = Field(default=None, description="Optional destination URL/path.")
    title: str | None = Field(default=None, description="Item title.")
    description: str | None = Field(default=None, description="Item description.")
    preview_image: str | None = Field(
        default=None, description="Item preview image URL."
    )


class ColumnCreateAttributes(CreateAttributes):
    """A column child inside a columns container."""

    width: Literal[1, 2, 3] = Field(description="Relative column width (1, 2 or 3).")


class ColumnPatchAttributes(PatchAttributes):
    """Patch attributes for columns."""

    width: Literal[1, 2, 3] | None = Field(
        default=None, description="Relative column width (1, 2 or 3)."
    )


class AccordionPanelCreateAttributes(CreateAttributes):
    """Accordion panel."""

    title: str = Field(description="Panel title.")


class AccordionPanelPatchAttributes(PatchAttributes):
    """Patch attributes for accordion panels."""

    title: str | None = Field(default=None, description="Panel title.")


class SliderCreateAttributes(CreateAttributes):
    """Slider container."""

    autoplay: bool = Field(description="Whether slides autoplay.")
    autoplay_delay: int = Field(description="Delay between slides in milliseconds.")
    autoplay_jump: bool = Field(description="Whether autoplay jumps without animation.")
    hide_arrows: bool = Field(description="Whether navigation arrows are hidden.")


class SliderPatchAttributes(PatchAttributes):
    """Patch attributes for sliders."""

    autoplay: bool | None = Field(default=None, description="Whether slides autoplay.")
    autoplay_delay: int | None = Field(
        default=None, description="Delay between slides in milliseconds."
    )
    autoplay_jump: bool | None = Field(
        default=None, description="Whether autoplay jumps without animation."
    )
    hide_arrows: bool | None = Field(
        default=None, description="Whether navigation arrows are hidden."
    )


class CarouselCreateAttributes(CreateAttributes):
    """Carousel container."""

    headline: str = Field(description="Carousel headline.")
    visible_items: int = Field(description="Number of visible items.")
    hide_description: bool = Field(description="Whether descriptions are hidden.")


class CarouselPatchAttributes(PatchAttributes):
    """Patch attributes for carousels."""

    headline: str | None = Field(default=None, description="Carousel headline.")
    visible_items: int | None = Field(
        default=None, description="Number of visible items."
    )
    hide_description: bool | None = Field(
        default=None, description="Whether descriptions are hidden."
    )


class ColumnsCreateAttributes(CreateAttributes):
    """Columns container."""

    reverse_wrap: bool = Field(description="Whether wrapping order is reversed.")


class ColumnsPatchAttributes(PatchAttributes):
    """Patch attributes for columns containers."""

    reverse_wrap: bool | None = Field(
        default=None, description="Whether wrapping order is reversed."
    )


class AccordionCreateAttributes(CreateAttributes):
    """Accordion container."""

    headline: str = Field(description="Headline shown above accordion.")
    title: str = Field(description="Accordion title text.")
    right_arrows: bool = Field(description="Show right-facing chevrons.")
    exclusive: bool = Field(description="Only one panel can be open at a time.")
    collapsed: bool = Field(description="All panels start collapsed.")
    filtering: bool = Field(description="Enable panel filtering UI.")


class AccordionPatchAttributes(PatchAttributes):
    """Patch attributes for accordion containers."""

    headline: str | None = Field(
        default=None, description="Headline shown above accordion."
    )
    title: str | None = Field(default=None, description="Accordion title text.")
    right_arrows: bool | None = Field(
        default=None, description="Show right-facing chevrons."
    )
    exclusive: bool | None = Field(
        default=None, description="Only one panel can be open at a time."
    )
    collapsed: bool | None = Field(
        default=None, description="All panels start collapsed."
    )
    filtering: bool | None = Field(
        default=None, description="Enable panel filtering UI."
    )


_HTML_DESCRIPTION = (
    "HTML content. Allowed tags: p, h2, h3, ul, ol, li, blockquote, "
    "a, br, strong, b, em, i, u, s, del, code. Only 'href' on <a>."
)


class QuoteCreateAttributes(CreateAttributes):
    """Blockquote with attribution."""

    html: str = Field(description="Quote text HTML. " + _HTML_DESCRIPTION)
    source_html: str = Field(default="", description="Attribution HTML.")
    extra_html: str = Field(default="", description="Extra context HTML.")
    variation: Literal["default", "testimonial"] = Field(
        default="default", description="Visual variation."
    )
    position: Literal["left", "center", "right"] | None = Field(
        default=None, description="Quote alignment."
    )
    reversed: bool = Field(default=False, description="Show source before quote.")
    title_html: str = Field(default="", description="Testimonial title HTML.")
    image_url: str = Field(default="", description="Testimonial image URL.")


class QuotePatchAttributes(PatchAttributes):
    """Patch attributes for quote blocks."""

    html: str | None = Field(default=None, description="Quote text HTML.")
    source_html: str | None = Field(default=None, description="Attribution HTML.")
    extra_html: str | None = Field(default=None, description="Extra context HTML.")
    variation: Literal["default", "testimonial"] | None = Field(
        default=None, description="Visual variation."
    )
    position: Literal["left", "center", "right"] | None = Field(
        default=None, description="Quote alignment."
    )
    reversed: bool | None = Field(default=None, description="Show source before quote.")
    title_html: str | None = Field(default=None, description="Testimonial title HTML.")
    image_url: str | None = Field(default=None, description="Testimonial image URL.")


class StatisticItemCreateAttributes(CreateAttributes):
    """A single statistic / KPI value."""

    value: str = Field(description="The number or metric (e.g. '35,000').")
    label: str = Field(description="Label below the value (e.g. 'Students').")
    info: str = Field(default="", description="Extra info text.")
    link: str = Field(default="", description="Link URL.")
    prefix: str = Field(default="", description="Text before animated value.")
    suffix: str = Field(default="", description="Text after animated value.")


class StatisticItemPatchAttributes(PatchAttributes):
    """Patch attributes for statistic items."""

    value: str | None = Field(default=None, description="The number or metric.")
    label: str | None = Field(default=None, description="Label below the value.")
    info: str | None = Field(default=None, description="Extra info text.")
    link: str | None = Field(default=None, description="Link URL.")
    prefix: str | None = Field(default=None, description="Text before animated value.")
    suffix: str | None = Field(default=None, description="Text after animated value.")


class StatisticCreateAttributes(CreateAttributes):
    """Statistic / KPI display container."""

    horizontal: bool = Field(default=False, description="Horizontal layout.")
    inverted: bool = Field(default=False, description="Dark background.")
    size: Literal["mini", "tiny", "small", "large", "huge"] = Field(
        default="small", description="Display size."
    )
    widths: Literal[1, 2, 3, 4] = Field(default=1, description="Column count (1-4).")
    animation_enabled: bool = Field(
        default=False, description="Enable count-up animation."
    )
    animation_duration: float = Field(
        default=5.0, description="Animation duration in seconds."
    )
    animation_decimals: int = Field(
        default=0, description="Decimal places in animated number."
    )


class StatisticPatchAttributes(PatchAttributes):
    """Patch attributes for statistic containers."""

    horizontal: bool | None = Field(default=None, description="Horizontal layout.")
    inverted: bool | None = Field(default=None, description="Dark background.")
    size: Literal["mini", "tiny", "small", "large", "huge"] | None = Field(
        default=None, description="Display size."
    )
    widths: Literal[1, 2, 3, 4] | None = Field(
        default=None, description="Column count (1-4)."
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


class FormTextFieldCreateAttributes(CreateAttributes):
    """Text input form field."""

    label: str = Field(description="Field label.")
    description: str = Field(default="", description="Help text.")
    required: bool = Field(default=False, description="Required field.")


class FormTextFieldPatchAttributes(PatchAttributes):
    """Patch attributes for text form fields."""

    label: str | None = Field(default=None, description="Field label.")
    description: str | None = Field(default=None, description="Help text.")
    required: bool | None = Field(default=None, description="Required field.")


class FormTextareaFieldCreateAttributes(CreateAttributes):
    """Multiline text form field."""

    label: str = Field(description="Field label.")
    description: str = Field(default="", description="Help text.")
    required: bool = Field(default=False, description="Required field.")


class FormTextareaFieldPatchAttributes(PatchAttributes):
    """Patch attributes for textarea form fields."""

    label: str | None = Field(default=None, description="Field label.")
    description: str | None = Field(default=None, description="Help text.")
    required: bool | None = Field(default=None, description="Required field.")


class FormNumberFieldCreateAttributes(CreateAttributes):
    """Number input form field."""

    label: str = Field(description="Field label.")
    description: str = Field(default="", description="Help text.")
    required: bool = Field(default=False, description="Required field.")


class FormNumberFieldPatchAttributes(PatchAttributes):
    """Patch attributes for number form fields."""

    label: str | None = Field(default=None, description="Field label.")
    description: str | None = Field(default=None, description="Help text.")
    required: bool | None = Field(default=None, description="Required field.")


class FormEmailFieldCreateAttributes(CreateAttributes):
    """Email input form field."""

    label: str = Field(description="Field label.")
    description: str = Field(default="", description="Help text.")
    required: bool = Field(default=False, description="Required field.")
    use_as_reply_to: bool = Field(default=False, description="Use as reply-to address.")


class FormEmailFieldPatchAttributes(PatchAttributes):
    """Patch attributes for email form fields."""

    label: str | None = Field(default=None, description="Field label.")
    description: str | None = Field(default=None, description="Help text.")
    required: bool | None = Field(default=None, description="Required field.")
    use_as_reply_to: bool | None = Field(
        default=None, description="Use as reply-to address."
    )


class FormSelectFieldCreateAttributes(CreateAttributes):
    """Dropdown select form field."""

    label: str = Field(description="Field label.")
    description: str = Field(default="", description="Help text.")
    required: bool = Field(default=False, description="Required field.")
    options: list[str] = Field(description="Dropdown options.")


class FormSelectFieldPatchAttributes(PatchAttributes):
    """Patch attributes for select form fields."""

    label: str | None = Field(default=None, description="Field label.")
    description: str | None = Field(default=None, description="Help text.")
    required: bool | None = Field(default=None, description="Required field.")
    options: list[str] | None = Field(default=None, description="Dropdown options.")


class FormRadioFieldCreateAttributes(CreateAttributes):
    """Radio button form field."""

    label: str = Field(description="Field label.")
    description: str = Field(default="", description="Help text.")
    required: bool = Field(default=False, description="Required field.")
    options: list[str] = Field(description="Radio options.")


class FormRadioFieldPatchAttributes(PatchAttributes):
    """Patch attributes for radio form fields."""

    label: str | None = Field(default=None, description="Field label.")
    description: str | None = Field(default=None, description="Help text.")
    required: bool | None = Field(default=None, description="Required field.")
    options: list[str] | None = Field(default=None, description="Radio options.")


class FormCheckboxFieldCreateAttributes(CreateAttributes):
    """Checkbox group form field."""

    label: str = Field(description="Field label.")
    description: str = Field(default="", description="Help text.")
    required: bool = Field(default=False, description="Required field.")
    options: list[str] = Field(default_factory=list, description="Checkbox options.")


class FormCheckboxFieldPatchAttributes(PatchAttributes):
    """Patch attributes for checkbox form fields."""

    label: str | None = Field(default=None, description="Field label.")
    description: str | None = Field(default=None, description="Help text.")
    required: bool | None = Field(default=None, description="Required field.")
    options: list[str] | None = Field(default=None, description="Checkbox options.")


class FormDateFieldCreateAttributes(CreateAttributes):
    """Date picker form field."""

    label: str = Field(description="Field label.")
    description: str = Field(default="", description="Help text.")
    required: bool = Field(default=False, description="Required field.")


class FormDateFieldPatchAttributes(PatchAttributes):
    """Patch attributes for date form fields."""

    label: str | None = Field(default=None, description="Field label.")
    description: str | None = Field(default=None, description="Help text.")
    required: bool | None = Field(default=None, description="Required field.")


class FormAttachmentFieldCreateAttributes(CreateAttributes):
    """File attachment form field."""

    label: str = Field(description="Field label.")
    description: str = Field(default="", description="Help text.")
    required: bool = Field(default=False, description="Required field.")


class FormAttachmentFieldPatchAttributes(PatchAttributes):
    """Patch attributes for attachment form fields."""

    label: str | None = Field(default=None, description="Field label.")
    description: str | None = Field(default=None, description="Help text.")
    required: bool | None = Field(default=None, description="Required field.")


class FormHiddenFieldCreateAttributes(CreateAttributes):
    """Hidden form field."""

    label: str = Field(description="Field name/identifier.")
    value: str = Field(description="Hidden value.")


class FormHiddenFieldPatchAttributes(PatchAttributes):
    """Patch attributes for hidden form fields."""

    label: str | None = Field(default=None, description="Field name/identifier.")
    value: str | None = Field(default=None, description="Hidden value.")


class FormCreateAttributes(CreateAttributes):
    """Form container."""

    title: str = Field(description="Form title.")
    description: str = Field(default="", description="Form description.")
    submit_label: str = Field(default="Submit", description="Submit button label.")
    show_cancel: bool = Field(default=False, description="Show cancel button.")
    cancel_label: str = Field(default="", description="Cancel button label.")
    recipient_email: str = Field(default="", description="Recipient email address.")
    subject: str = Field(default="", description="Email subject line.")


class FormPatchAttributes(PatchAttributes):
    """Patch attributes for form containers."""

    title: str | None = Field(default=None, description="Form title.")
    description: str | None = Field(default=None, description="Form description.")
    submit_label: str | None = Field(default=None, description="Submit button label.")
    show_cancel: bool | None = Field(default=None, description="Show cancel button.")
    cancel_label: str | None = Field(default=None, description="Cancel button label.")
    recipient_email: str | None = Field(
        default=None, description="Recipient email address."
    )
    subject: str | None = Field(default=None, description="Email subject line.")


class TabCreateAttributes(CreateAttributes):
    """Tab child inside a tabs container."""

    title: str = Field(description="Tab heading.")


class TabPatchAttributes(PatchAttributes):
    """Patch attributes for tabs."""

    title: str | None = Field(default=None, description="Tab heading.")


class TabsCreateAttributes(CreateAttributes):
    """Tabs container."""

    title: str = Field(default="", description="Block title.")
    description: str = Field(default="", description="Block description.")
    variation: Literal[
        "default",
        "accordion",
        "horizontal-responsive",
        "carousel-horizontal",
        "carousel-vertical",
    ] = Field(default="default", description="Display variation.")
    hide_empty_tabs: bool = Field(
        default=False, description="Hide tabs with no content."
    )


class TabsPatchAttributes(PatchAttributes):
    """Patch attributes for tabs containers."""

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


class PdfViewerCreateAttributes(CreateAttributes):
    """Embedded PDF viewer."""

    url: str = Field(description="PDF file URL or path.")
    initial_page: int = Field(default=1, description="Starting page number.")
    fit_page_width: bool = Field(
        default=True, description="Scale to fit container width."
    )
    hide_toolbar: bool = Field(default=False, description="Hide PDF toolbar.")
    hide_navbar: bool = Field(default=False, description="Hide navigation bar.")
    disable_scroll: bool = Field(default=False, description="Disable page scrolling.")
    click_to_download: bool = Field(
        default=False, description="Enable click-to-download."
    )
    show_pages_preview: bool = Field(
        default=False, description="Show thumbnail page previews."
    )


class PdfViewerPatchAttributes(PatchAttributes):
    """Patch attributes for PDF viewer blocks."""

    url: str | None = Field(default=None, description="PDF file URL or path.")
    initial_page: int | None = Field(default=None, description="Starting page number.")
    fit_page_width: bool | None = Field(
        default=None, description="Scale to fit container width."
    )
    hide_toolbar: bool | None = Field(default=None, description="Hide PDF toolbar.")
    hide_navbar: bool | None = Field(default=None, description="Hide navigation bar.")
    disable_scroll: bool | None = Field(
        default=None, description="Disable page scrolling."
    )
    click_to_download: bool | None = Field(
        default=None, description="Enable click-to-download."
    )
    show_pages_preview: bool | None = Field(
        default=None, description="Show thumbnail page previews."
    )


class MetadataPatchAttributes(PatchAttributes):
    """Patch attributes for page metadata. Only provided fields are changed."""

    title: str | None = Field(default=None, description="Page title.")
    description: str | None = Field(
        default=None, description="Page description / summary."
    )
    preview_image: str | None = Field(
        default=None, description="Page preview image URL."
    )
    subjects: list[str] | None = Field(
        default=None, description="Page tags / subjects."
    )


@dataclass(frozen=True)
class BlockSpec:
    """Single source of truth for a mutable block type."""

    type_name: str
    block_model: type[BaseModel]
    attributes_model: type[BaseModel]
    create_model: type[CreateAttributes]
    patch_model: type[PatchAttributes]
    create_description: str
    update_description: str


BLOCK_SPECS: tuple[BlockSpec, ...] = (
    BlockSpec(
        "title",
        TitleBlock,
        TitleAttributes,
        TitleCreateAttributes,
        TitlePatchAttributes,
        "Create a `title` element (page title).",
        "Patch attributes on an existing `title` element. Only provided fields are changed.",
    ),
    BlockSpec(
        "description",
        DescriptionBlock,
        DescriptionAttributes,
        DescriptionCreateAttributes,
        DescriptionPatchAttributes,
        "Create a `description` element (page description/summary).",
        "Patch attributes on an existing `description` element. Only provided fields are changed.",
    ),
    BlockSpec(
        "heading",
        HeadingBlock,
        HeadingAttributes,
        HeadingCreateAttributes,
        HeadingPatchAttributes,
        "Create a `heading` element (h2 or h3).",
        "Patch attributes on an existing `heading` element. Only provided fields are changed.",
    ),
    BlockSpec(
        "rich_text",
        RichTextBlock,
        RichTextAttributes,
        RichTextCreateAttributes,
        RichTextPatchAttributes,
        "Create a `rich_text` element. HTML must use the strict subset.",
        "Patch attributes on an existing `rich_text` element. HTML must use the strict subset.",
    ),
    BlockSpec(
        "image",
        ImageBlock,
        ImageAttributes,
        ImageCreateAttributes,
        ImagePatchAttributes,
        "Create an `image` element.",
        "Patch attributes on an existing `image` element. Only provided fields are changed.",
    ),
    BlockSpec(
        "divider",
        DividerBlock,
        DividerAttributes,
        DividerCreateAttributes,
        DividerPatchAttributes,
        "Create a `divider` element.",
        "Patch attributes on an existing `divider` element. Only provided fields are changed.",
    ),
    BlockSpec(
        "video",
        VideoBlock,
        VideoAttributes,
        VideoCreateAttributes,
        VideoPatchAttributes,
        "Create a `video` element.",
        "Patch attributes on an existing `video` element. Only provided fields are changed.",
    ),
    BlockSpec(
        "button",
        ButtonBlock,
        ButtonAttributes,
        ButtonCreateAttributes,
        ButtonPatchAttributes,
        "Create a `button` element.",
        "Patch attributes on an existing `button` element. Only provided fields are changed.",
    ),
    BlockSpec(
        "teaser",
        TeaserBlock,
        TeaserAttributes,
        TeaserCreateAttributes,
        TeaserPatchAttributes,
        "Create a `teaser` element.",
        "Patch attributes on an existing `teaser` element. Only provided fields are changed.",
    ),
    BlockSpec(
        "highlight",
        HighlightBlock,
        HighlightAttributes,
        HighlightCreateAttributes,
        HighlightPatchAttributes,
        "Create a `highlight` element. Body HTML must use the strict subset.",
        "Patch attributes on an existing `highlight` element. HTML must use the strict subset.",
    ),
    BlockSpec(
        "table",
        TableBlock,
        TableAttributes,
        TableCreateAttributes,
        TablePatchAttributes,
        "Create a `table` element.",
        "Patch attributes on an existing `table` element. Only provided fields are changed.",
    ),
    BlockSpec(
        "slide",
        SlideBlock,
        SlideAttributes,
        SlideCreateAttributes,
        SlidePatchAttributes,
        "Create a `slide` element inside a slider.",
        "Patch attributes on an existing `slide` element. Only provided fields are changed.",
    ),
    BlockSpec(
        "carousel_item",
        CarouselItemBlock,
        CarouselItemAttributes,
        CarouselItemCreateAttributes,
        CarouselItemPatchAttributes,
        "Create a `carousel_item` element inside a carousel.",
        "Patch attributes on an existing `carousel_item` element. Only provided fields are changed.",
    ),
    BlockSpec(
        "column",
        ColumnBlock,
        ColumnAttributes,
        ColumnCreateAttributes,
        ColumnPatchAttributes,
        "Create a `column` element inside a columns container.",
        "Patch attributes on an existing `column` element. Only provided fields are changed.",
    ),
    BlockSpec(
        "accordion_panel",
        AccordionPanelBlock,
        AccordionPanelAttributes,
        AccordionPanelCreateAttributes,
        AccordionPanelPatchAttributes,
        "Create an `accordion_panel` element inside an accordion.",
        "Patch attributes on an existing `accordion_panel` element. Only provided fields are changed.",
    ),
    BlockSpec(
        "slider",
        SliderBlock,
        SliderAttributes,
        SliderCreateAttributes,
        SliderPatchAttributes,
        "Create a `slider` container.",
        "Patch attributes on an existing `slider` container. Only provided fields are changed.",
    ),
    BlockSpec(
        "carousel",
        CarouselBlock,
        CarouselAttributes,
        CarouselCreateAttributes,
        CarouselPatchAttributes,
        "Create a `carousel` container.",
        "Patch attributes on an existing `carousel` container. Only provided fields are changed.",
    ),
    BlockSpec(
        "columns",
        ColumnsBlock,
        ColumnsAttributes,
        ColumnsCreateAttributes,
        ColumnsPatchAttributes,
        "Create a `columns` container.",
        "Patch attributes on an existing `columns` container. Only provided fields are changed.",
    ),
    BlockSpec(
        "accordion",
        AccordionBlock,
        AccordionAttributes,
        AccordionCreateAttributes,
        AccordionPatchAttributes,
        "Create an `accordion` container.",
        "Patch attributes on an existing `accordion` container. Only provided fields are changed.",
    ),
    BlockSpec(
        "quote",
        QuoteBlock,
        QuoteAttributes,
        QuoteCreateAttributes,
        QuotePatchAttributes,
        "Create a `quote` element (blockquote with attribution).",
        "Patch attributes on an existing `quote` element. Only provided fields are changed.",
    ),
    BlockSpec(
        "statistic_item",
        StatisticItemBlock,
        StatisticItemAttributes,
        StatisticItemCreateAttributes,
        StatisticItemPatchAttributes,
        "Create a `statistic_item` inside a statistic container.",
        "Patch attributes on an existing `statistic_item`. Only provided fields are changed.",
    ),
    BlockSpec(
        "statistic",
        StatisticBlock,
        StatisticAttributes,
        StatisticCreateAttributes,
        StatisticPatchAttributes,
        "Create a `statistic` container for KPI/metrics display.",
        "Patch attributes on an existing `statistic` container. Only provided fields are changed.",
    ),
    BlockSpec(
        "form_text_field",
        FormFieldBlock,
        FormTextFieldAttributes,
        FormTextFieldCreateAttributes,
        FormTextFieldPatchAttributes,
        "Create a `form_text_field` inside a form.",
        "Patch attributes on an existing `form_text_field`. Only provided fields are changed.",
    ),
    BlockSpec(
        "form_textarea_field",
        FormFieldBlock,
        FormTextFieldAttributes,
        FormTextareaFieldCreateAttributes,
        FormTextareaFieldPatchAttributes,
        "Create a `form_textarea_field` inside a form.",
        "Patch attributes on an existing `form_textarea_field`. Only provided fields are changed.",
    ),
    BlockSpec(
        "form_number_field",
        FormFieldBlock,
        FormTextFieldAttributes,
        FormNumberFieldCreateAttributes,
        FormNumberFieldPatchAttributes,
        "Create a `form_number_field` inside a form.",
        "Patch attributes on an existing `form_number_field`. Only provided fields are changed.",
    ),
    BlockSpec(
        "form_email_field",
        FormFieldBlock,
        FormEmailFieldAttributes,
        FormEmailFieldCreateAttributes,
        FormEmailFieldPatchAttributes,
        "Create a `form_email_field` inside a form.",
        "Patch attributes on an existing `form_email_field`. Only provided fields are changed.",
    ),
    BlockSpec(
        "form_select_field",
        FormFieldBlock,
        FormSelectFieldAttributes,
        FormSelectFieldCreateAttributes,
        FormSelectFieldPatchAttributes,
        "Create a `form_select_field` (dropdown) inside a form.",
        "Patch attributes on an existing `form_select_field`. Only provided fields are changed.",
    ),
    BlockSpec(
        "form_radio_field",
        FormFieldBlock,
        FormSelectFieldAttributes,
        FormRadioFieldCreateAttributes,
        FormRadioFieldPatchAttributes,
        "Create a `form_radio_field` inside a form.",
        "Patch attributes on an existing `form_radio_field`. Only provided fields are changed.",
    ),
    BlockSpec(
        "form_checkbox_field",
        FormFieldBlock,
        FormSelectFieldAttributes,
        FormCheckboxFieldCreateAttributes,
        FormCheckboxFieldPatchAttributes,
        "Create a `form_checkbox_field` inside a form.",
        "Patch attributes on an existing `form_checkbox_field`. Only provided fields are changed.",
    ),
    BlockSpec(
        "form_date_field",
        FormFieldBlock,
        FormTextFieldAttributes,
        FormDateFieldCreateAttributes,
        FormDateFieldPatchAttributes,
        "Create a `form_date_field` inside a form.",
        "Patch attributes on an existing `form_date_field`. Only provided fields are changed.",
    ),
    BlockSpec(
        "form_attachment_field",
        FormFieldBlock,
        FormTextFieldAttributes,
        FormAttachmentFieldCreateAttributes,
        FormAttachmentFieldPatchAttributes,
        "Create a `form_attachment_field` inside a form.",
        "Patch attributes on an existing `form_attachment_field`. Only provided fields are changed.",
    ),
    BlockSpec(
        "form_hidden_field",
        FormFieldBlock,
        FormHiddenFieldAttributes,
        FormHiddenFieldCreateAttributes,
        FormHiddenFieldPatchAttributes,
        "Create a `form_hidden_field` inside a form.",
        "Patch attributes on an existing `form_hidden_field`. Only provided fields are changed.",
    ),
    BlockSpec(
        "form",
        FormBlock,
        FormAttributes,
        FormCreateAttributes,
        FormPatchAttributes,
        "Create a `form` container.",
        "Patch attributes on an existing `form` container. Only provided fields are changed.",
    ),
    BlockSpec(
        "tab",
        TabBlock,
        TabAttributes,
        TabCreateAttributes,
        TabPatchAttributes,
        "Create a `tab` inside a tabs container.",
        "Patch attributes on an existing `tab`. Only provided fields are changed.",
    ),
    BlockSpec(
        "tabs",
        TabsBlock,
        TabsAttributes,
        TabsCreateAttributes,
        TabsPatchAttributes,
        "Create a `tabs` container.",
        "Patch attributes on an existing `tabs` container. Only provided fields are changed.",
    ),
    BlockSpec(
        "pdf_viewer",
        PdfViewerBlock,
        PdfViewerAttributes,
        PdfViewerCreateAttributes,
        PdfViewerPatchAttributes,
        "Create a `pdf_viewer` element.",
        "Patch attributes on an existing `pdf_viewer` element. Only provided fields are changed.",
    ),
)

BLOCK_SPECS_BY_TYPE: dict[str, BlockSpec] = {
    spec.type_name: spec for spec in BLOCK_SPECS
}

CHILD_TYPES: dict[str, str] = {
    "slider": "slide",
    "carousel": "carousel_item",
    "columns": "column",
    "accordion": "accordion_panel",
    "statistic": "statistic_item",
    "tabs": "tab",
}

FORM_FIELD_TYPES = {
    "form_text_field",
    "form_textarea_field",
    "form_number_field",
    "form_email_field",
    "form_select_field",
    "form_radio_field",
    "form_checkbox_field",
    "form_date_field",
    "form_attachment_field",
    "form_hidden_field",
}

RESTRICTED_CHILD_TYPES: dict[str, set[str]] = {
    "form": FORM_FIELD_TYPES | {"rich_text"},
}

CHILD_ONLY_TYPES = set(CHILD_TYPES.values()) | FORM_FIELD_TYPES

OPEN_CONTAINER_TYPES = {"column", "accordion_panel", "tab"}

CONTAINER_TYPES = (
    set(CHILD_TYPES.keys()) | OPEN_CONTAINER_TYPES | set(RESTRICTED_CHILD_TYPES.keys())
)

PARENT_NEEDED: dict[str, str] = {v: k for k, v in CHILD_TYPES.items()}
for _ft in FORM_FIELD_TYPES:
    PARENT_NEEDED[_ft] = "form"
