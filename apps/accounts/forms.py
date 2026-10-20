from django import forms
from django.contrib.auth.password_validation import validate_password

from .models import LearningFormat, User


class SignupForm(forms.Form):
    name = forms.CharField(max_length=150)
    email = forms.EmailField()
    password1 = forms.CharField(widget=forms.PasswordInput, label="Password")
    password2 = forms.CharField(widget=forms.PasswordInput, label="Confirm password")

    def clean_name(self):
        return self.cleaned_data["name"].strip()

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2:
            if p1 != p2:
                self.add_error("password2", "Passwords do not match.")
            else:
                # pass an unsaved user so "password too similar to email/name" is checked
                candidate = User(email=cleaned.get("email", ""), name=cleaned.get("name", ""))
                try:
                    validate_password(p1, user=candidate)
                except forms.ValidationError as exc:
                    self.add_error("password1", exc)
        return cleaned


class PreferredFormatForm(forms.Form):
    preferred_format = forms.ChoiceField(choices=LearningFormat.choices)