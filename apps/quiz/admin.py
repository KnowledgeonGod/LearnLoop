from django.contrib import admin

from .models import QuizAttempt


@admin.register(QuizAttempt)
class QuizAttemptAdmin(admin.ModelAdmin):
    list_display = ("user", "topic", "level", "goal", "preferred_format", "updated_at")
    list_select_related = ("user", "topic")  # avoids N+1 when listing
    list_filter = ("level", "goal")