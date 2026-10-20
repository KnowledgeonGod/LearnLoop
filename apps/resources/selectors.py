from collections import defaultdict

from .models import StepResource


def resources_by_step(plan) -> dict:
    """{step_id: [Resource, ...]} for one plan, in a single query.
    The caller passes a plan it already fetched for the current user."""
    links = (
        StepResource.objects.filter(step__plan=plan)
        .select_related("resource")
        .order_by("step_id", "position")
    )
    result = defaultdict(list)
    for link in links:
        result[link.step_id].append(link.resource)
    return dict(result)