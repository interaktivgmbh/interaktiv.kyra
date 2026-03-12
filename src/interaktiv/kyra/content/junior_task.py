from plone.supermodel import model
from zope import schema


class IJuniorTask(model.Schema):
    """Schema for JuniorTask content type."""

    status = schema.TextLine(
        title=u"Status",
        description=u"Task status, e.g. offen, in_arbeit, done",
        required=False,
    )

    task_date = schema.Date(
        title=u"Task Date",
        description=u"Due date for this task",
        required=False,
    )
