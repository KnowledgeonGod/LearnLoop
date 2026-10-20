from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django_ratelimit.decorators import ratelimit

from apps.topics.models import Topic

from .forms import QuizForm
from .selectors import initial_answers
from .services import submit_quiz


@login_required
@ratelimit(key="user", rate="20/m", method="POST", block=True)
def quiz(request, topic_id):
    topic = get_object_or_404(Topic, pk=topic_id)  # topics are shared, so an ID is fine
    # Answers are always looked up for request.user, never for a user ID from the URL (IDOR).
    if request.method == "POST":
        form = QuizForm(request.POST)
        if form.is_valid():
            submit_quiz(user=request.user, topic=topic, answers=form.cleaned_data)
            return redirect("topic_detail", topic_id=topic.pk)
    else:
        form = QuizForm(initial=initial_answers(request.user, topic))
    return render(request, "quiz/quiz.html", {"form": form, "topic": topic})