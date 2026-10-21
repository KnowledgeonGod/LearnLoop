from django.contrib import admin

from .models import ResourceFeedback


@admin.register(ResourceFeedback)
class ResourceFeedbackAdmin(admin.ModelAdmin):
    list_display = ("user", "resource", "rating", "progress", "updated_at")
    list_select_related = ("user", "resource")  # avoids N+1 when listing
    list_filter = ("rating", "progress")