from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django_ratelimit.decorators import ratelimit

from .forms import PreferredFormatForm, SignupForm
from .services import (
    EmailAlreadyRegistered,
    ensure_profile,
    register_user,
    set_preferred_format,
)


@ratelimit(key="ip", rate="10/h", method="POST", block=True)  # rate limiting
def signup(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            user = register_user(
                name=data["name"], email=data["email"], password=data["password1"]
            )
        except EmailAlreadyRegistered:
            form.add_error("email", "An account with this email already exists.")
        else:
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            return redirect("dashboard")
    return render(request, "accounts/signup.html", {"form": form})





@login_required
def profile(request):
    # Authorization / IDOR: no ID in the URL. The profile is always request.user's own,
    # so one user can never reach another user's data by changing a number.
    current = ensure_profile(request.user)
    form = PreferredFormatForm(
        request.POST or None, initial={"preferred_format": current.preferred_format}
    )
    if request.method == "POST" and form.is_valid():
        set_preferred_format(user=request.user, value=form.cleaned_data["preferred_format"])
        return redirect("dashboard")
    return render(request, "accounts/profile.html", {"form": form})