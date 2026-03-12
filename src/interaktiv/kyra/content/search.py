from plone import api


def search_junior_tasks(status=None):
    """Search JuniorTask objects via the catalog.

    Returns catalog brains with title, status and task_date.
    """
    query = {
        "portal_type": "JuniorTask",
        "sort_on": "created",
        "sort_order": "reverse",
    }

    if status:
        query["status"] = status

    brains = api.content.find(**query)

    return [
        {
            "title": brain.Title,
            "status": brain.status,
            "task_date": brain.task_date,
            "url": brain.getURL(),
            "created": brain.created,
        }
        for brain in brains
    ]
