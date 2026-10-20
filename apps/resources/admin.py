from django.contrib import admin

from .models import Resource


@admin.register(Resource)
class ResourceAdmin(admin.ModelAdmin):
    list_display = ("title", "topic", "kind", "source", "fetched_at")
    list_select_related = ("topic",)  # avoids N+1 when listing
    list_filter = ("kind", "source")
    search_fields = ("title", "url")