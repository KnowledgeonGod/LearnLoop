from django.contrib import admin

from .models import LearningPlan, LLMUsage, PlanJob, PlanUsage


@admin.register(LearningPlan)
class LearningPlanAdmin(admin.ModelAdmin):
    list_display = ("user", "topic", "source", "generated_at")
    list_select_related = ("user", "topic")  # avoids N+1 when listing


@admin.register(PlanJob)
class PlanJobAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "topic", "status", "created_at", "started_at", "finished_at")
    list_select_related = ("user", "topic", "plan")
    list_filter = ("status",)
    search_fields = ("user__email", "topic__name", "error")
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "started_at", "finished_at")


@admin.register(PlanUsage)
class PlanUsageAdmin(admin.ModelAdmin):
    list_display = ("user", "day", "count")
    list_select_related = ("user",)
    list_filter = ("day",)
    search_fields = ("user__email",)
    date_hierarchy = "day"
    readonly_fields = ("user", "day", "count")

    def has_add_permission(self, request):
        return False


@admin.register(LLMUsage)
class LLMUsageAdmin(admin.ModelAdmin):
    list_display = ("day", "model", "calls", "prompt_tokens", "completion_tokens")
    list_filter = ("day", "model")
    date_hierarchy = "day"
    readonly_fields = ("day", "model", "calls", "prompt_tokens", "completion_tokens")

    def has_add_permission(self, request):
        return False
