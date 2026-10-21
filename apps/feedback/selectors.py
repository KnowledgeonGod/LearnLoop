from .models import ResourceFeedback


def feedback_for(user, resources) -> dict:
    """{resource_id: ResourceFeedback} for this user, in one query."""
    ids = [resource.pk for resource in resources]
    if not ids:
        return {}
    rows = ResourceFeedback.objects.filter(user=user, resource_id__in=ids)  # only their own
    return {row.resource_id: row for row in rows}