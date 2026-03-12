from plone.indexer import indexer

from .junior_task import IJuniorTask


@indexer(IJuniorTask)
def status_indexer(obj):
    return getattr(obj, "status", None)


@indexer(IJuniorTask)
def task_date_indexer(obj):
    return getattr(obj, "task_date", None)
