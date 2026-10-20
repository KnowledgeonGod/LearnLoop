from django.contrib import admin

from .models import User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("email", "name", "date_joined", "preferred_format")
    list_select_related = ("profile",)  # one JOIN, not one extra query per row
    search_fields = ("email", "name")
    ordering = ("-date_joined",)
    exclude = ("password",)  # never edit raw hashes here

    def has_add_permission(self, request):
        return False

    @admin.display(description="Format")
    def preferred_format(self, obj):
        profile = getattr(obj, "profile", None)  # superusers may not have one
        return profile.preferred_format if profile else "-"