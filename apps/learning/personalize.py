from apps.feedback.selectors import ranking_signals
from apps.quiz.selectors import get_attempt
from services import ranking
from services.ranking import NO_SIGNALS


def _level(user, topic):
    attempt = get_attempt(user, topic)  # always this user's own answers
    return attempt.level if attempt else None


def ranker_for(user, topic):
    """A function that orders one step type's resources for this person."""
    level = _level(user, topic)

    def rank(resources):
        signals = ranking_signals(user, resources)  # only this user's feedback
        return ranking.rank(resources, signals, level)

    return rank


def reasons_for(user, topic, resources) -> dict:
    """{resource_id: [short reasons]} for the resources on a plan page. Two queries in
    total, however many resources, and none at all for an empty plan."""
    resources = list(resources)
    if not resources:
        return {}
    level = _level(user, topic)
    signals = ranking_signals(user, resources)
    return {
        r.pk: ranking.reasons(
            ranking.score_parts(
                title=r.title, source=r.source, signals=signals.get(r.pk, NO_SIGNALS), level=level
            )
        )
        for r in resources
    }
