from django.db.models import Q

from services.ranking import Signals

from .models import Progress, Rating, ResourceFeedback


def feedback_for(user, resources) -> dict:
    """{resource_id: ResourceFeedback} for this user, in one query."""
    ids = [resource.pk for resource in resources]
    if not ids:
        return {}
    rows = ResourceFeedback.objects.filter(user=user, resource_id__in=ids)  # only their own
    return {row.resource_id: row for row in rows}


def ranking_signals(user, resources) -> dict:
    """{resource_id: Signals} from THIS user's own feedback only, in one query.

    Another learner's likes and dislikes are deliberately never read: one person's
    taste must not change what somebody else is shown."""
    ids = {resource.pk for resource in resources}
    if not ids:
        return {}

    own = {
        row.resource_id: row
        for row in ResourceFeedback.objects.filter(user=user, resource_id__in=ids)
    }
    result = {}
    for resource_id in ids:
        row = own.get(resource_id)
        result[resource_id] = Signals(
            rating=row.rating if row else None,
            progress=row.progress if row else None,
        )
    return result


def hidden_feedback_for(user, topic):
    """This user's feedback rows that hide a resource from their plan for one topic.

    The plan page lists these separately so a dislike or skip can be undone; a hidden
    resource is never shown inside the plan itself again."""
    return (
        ResourceFeedback.objects.filter(user=user, resource__topic=topic)
        .filter(Q(rating=Rating.DISLIKE.value) | Q(progress=Progress.SKIPPED.value))
        .select_related("resource")
        .order_by("resource__title")
    )
