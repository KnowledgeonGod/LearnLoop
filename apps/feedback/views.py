from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from apps.resources.models import Resource

from .services import InvalidFeedback, set_feedback


@login_required
@ratelimit(key="user", rate="60/m", method="POST", block=True)
@require_POST
def feedback(request, resource_id):
    # Authorization: only resources shown in one of THIS user's own plans.
    # Anything else is a 404, so nobody can probe which resources exist.
    resource = get_object_or_404(
        Resource.objects.filter(step_links__step__plan__user=request.user).distinct(),
        pk=resource_id,
    )
    try:
        set_feedback(user=request.user, resource=resource, action=request.POST.get("action", ""))
    except InvalidFeedback:
        return HttpResponseBadRequest("Unknown action.")
    # The redirect target comes from the database, never from the request (no open redirect).
    return redirect("plan", topic_id=resource.topic_id)