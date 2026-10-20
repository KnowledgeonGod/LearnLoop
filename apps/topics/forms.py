from django import forms

from .services import MAX_TOPIC_LENGTH, InvalidTopic, clean_topic_name


class TopicSearchForm(forms.Form):
    query = forms.CharField(max_length=MAX_TOPIC_LENGTH, label="What do you want to learn?")

    def clean_query(self):
        try:
            return clean_topic_name(self.cleaned_data["query"])  # same rule as the service
        except InvalidTopic as exc:
            raise forms.ValidationError(str(exc))