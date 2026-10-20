from django import forms

from .models import ANSWER_FIELDS, QuizAttempt


class QuizForm(forms.ModelForm):
    class Meta:
        model = QuizAttempt
        fields = ANSWER_FIELDS
        labels = {
            "level": "How much do you already know?",
            "goal": "What's your goal?",
            "hours_per_week": "Hours per week you can study",
            "weeks_available": "How many weeks do you have?",
            "preferred_format": "How do you prefer to learn?",
            "depth": "How deep do you want to go?",
        }
        widgets = {
            "level": forms.RadioSelect,
            "goal": forms.RadioSelect,
            "preferred_format": forms.RadioSelect,
            "depth": forms.RadioSelect,
        }