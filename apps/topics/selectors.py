from django.db.models import Count

from .models import Topic, UserTopic

# Privacy: a topic only appears publicly once this many different users searched it,
# so one person's private search never shows up on everyone's dashboard.
MIN_SEARCHERS_FOR_POPULAR = 2


def recent_topics_for(user, limit: int = 8) -> list:
    # Always filtered by the user passed in, so history is never shared.
    # select_related fetches each topic in the same query (no N+1).
    return list(
        UserTopic.objects.filter(user=user)
        .select_related("topic")
        .order_by("-last_searched_at")[:limit]
    )


def popular_topics(limit: int = 8) -> list:
    # One UserTopic row per (user, topic), so Count = number of distinct users.
    return list(
        Topic.objects.annotate(searchers=Count("searches"))
        .filter(searchers__gte=MIN_SEARCHERS_FOR_POPULAR)
        .order_by("-searchers", "name")[:limit]
    )