from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from .forms import TopicSearchForm
from .models import Topic
from .selectors import popular_topics, recent_topics_for
from .services import record_search


def _dashboard_context(user, form):
    return {"form": form, "recent": recent_topics_for(user), "popular": popular_topics()}


@login_required
def dashboard(request):
    context = _dashboard_context(request.user, TopicSearchForm())
    return render(request, "topics/dashboard.html", context)


@login_required
@ratelimit(key="user", rate="30/m", method="POST", block=True)
@require_POST
def search(request):
    form = TopicSearchForm(request.POST)
    if not form.is_valid():
        context = _dashboard_context(request.user, form)
        return render(request, "topics/dashboard.html", context, status=400)
    topic = record_search(user=request.user, raw_name=form.cleaned_data["query"])
    return redirect("topic_detail", topic_id=topic.pk)


@login_required
def topic_detail(request, topic_id):
    # Topics are shared and not private, so an ID in the URL is fine here.
    # (Per-user data, like history, never takes an ID from the URL.)
    topic = get_object_or_404(Topic, pk=topic_id)
    return render(request, "topics/topic_detail.html", {"topic": topic})