from django.contrib import admin

from .models import LearningPlan


@admin.register(LearningPlan)
class LearningPlanAdmin(admin.ModelAdmin):
    list_display = ("user", "topic", "source", "generated_at")
    list_select_related = ("user", "topic")  # avoids N+1 when listing