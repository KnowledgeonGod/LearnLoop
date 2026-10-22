from django.contrib import admin

from .models import Resource, ResourceFetch


@admin.register(Resource)
class ResourceAdmin(admin.ModelAdmin):
    list_display = ("title", "topic", "kind", "source", "fetched_at")
    list_select_related = ("topic",)  # avoids N+1 when listing
    list_filter = ("kind", "source")
    search_fields = ("title", "url")
    date_hierarchy = "fetched_at"


@admin.register(ResourceFetch)
class ResourceFetchAdmin(admin.ModelAdmin):
    list_display = ("topic", "step_type", "page", "fetched_at")
    list_select_related = ("topic",)
    list_filter = ("step_type", "fetched_at")
    search_fields = ("topic__name",)
    date_hierarchy = "fetched_at"
    ordering = ("-fetched_at",)
