from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.accounts.models import LearningFormat, User
from apps.accounts.services import ensure_profile
from apps.feedback.models import Progress, Rating
from apps.feedback.services import ACTIONS
from apps.learning.models import PlanJob, StepType
from apps.learning.selectors import NEEDS_QUIZ, NO_PLAN, READY, STALE, get_plan_page
from apps.quiz.models import Depth, Goal, Level
from apps.resources.models import ResourceKind
from apps.topics.models import Topic
from apps.topics.services import MAX_TOPIC_LENGTH, InvalidTopic, clean_topic_name

PLAN_STATES = [
    (NEEDS_QUIZ, "The quiz has not been taken for this topic yet"),
    (NO_PLAN, "Quiz taken, no plan generated yet"),
    (STALE, "A plan exists but the quiz answers changed since it was built"),
    (READY, "The plan is up to date"),
]


class ErrorSerializer(serializers.Serializer):
    detail = serializers.CharField()
    code = serializers.CharField(required=False)


# ---------- auth and profile ----------

class SignupSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False, style={"input_type": "password"})

    def validate_email(self, value):
        return value.strip().lower()

    def validate(self, attrs):
        candidate = User(email=attrs["email"], name=attrs["name"])
        try:
            validate_password(attrs["password"], user=candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)})
        return attrs


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False, style={"input_type": "password"})


class UserSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(read_only=True)
    email = serializers.EmailField(read_only=True)
    date_joined = serializers.DateTimeField(read_only=True)
    preferred_format = serializers.SerializerMethodField()

    @extend_schema_field(serializers.ChoiceField(choices=LearningFormat.choices))
    def get_preferred_format(self, user):
        return ensure_profile(user).preferred_format


class AuthResponseSerializer(serializers.Serializer):
    token = serializers.CharField(help_text="Send as the header `Authorization: Token <token>`.")
    user = UserSerializer()


class ProfileUpdateSerializer(serializers.Serializer):
    preferred_format = serializers.ChoiceField(choices=LearningFormat.choices)


# ---------- topics ----------

class TopicSerializer(serializers.ModelSerializer):
    class Meta:
        model = Topic
        fields = ("id", "name", "created_at")
        read_only_fields = fields


class TopicDetailSerializer(TopicSerializer):
    plan_state = serializers.SerializerMethodField()

    class Meta(TopicSerializer.Meta):
        fields = ("id", "name", "created_at", "plan_state")
        read_only_fields = fields

    @extend_schema_field(serializers.ChoiceField(choices=PLAN_STATES))
    def get_plan_state(self, topic):
        return get_plan_page(self.context["request"].user, topic).state


class TopicSearchSerializer(serializers.Serializer):
    query = serializers.CharField(
        max_length=MAX_TOPIC_LENGTH,
        help_text="What the user wants to learn, e.g. 'python decorators'. 2 to 100 characters.",
    )

    def validate_query(self, value):
        try:
            return clean_topic_name(value)
        except InvalidTopic as exc:
            raise serializers.ValidationError(str(exc))


class RecentTopicSerializer(serializers.Serializer):
    topic = TopicSerializer()
    last_searched_at = serializers.DateTimeField()


class DashboardSerializer(serializers.Serializer):
    recent = RecentTopicSerializer(many=True)
    popular = TopicSerializer(many=True)


# ---------- quiz ----------

class QuizAnswersSerializer(serializers.Serializer):
    level = serializers.ChoiceField(choices=Level.choices)
    goal = serializers.ChoiceField(choices=Goal.choices)
    hours_per_week = serializers.IntegerField(min_value=1, max_value=40)
    weeks_available = serializers.IntegerField(min_value=1, max_value=52)
    preferred_format = serializers.ChoiceField(choices=LearningFormat.choices)
    depth = serializers.ChoiceField(choices=Depth.choices)


class QuizStateSerializer(QuizAnswersSerializer):
    completed = serializers.BooleanField(
        read_only=True,
        help_text="False when the user has not submitted this quiz yet; the other fields are then defaults.",
    )


# ---------- plan ----------

class FeedbackStateSerializer(serializers.Serializer):
    rating = serializers.ChoiceField(choices=Rating.choices, allow_null=True)
    progress = serializers.ChoiceField(choices=Progress.choices, allow_null=True)


class PlanResourceSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    url = serializers.URLField()
    kind = serializers.ChoiceField(choices=ResourceKind.choices)
    source = serializers.CharField(help_text="Where it came from, e.g. youtube or wikipedia.")
    why_this_one = serializers.ListField(child=serializers.CharField())
    feedback = FeedbackStateSerializer(allow_null=True, help_text="This user's own feedback, or null.")


class PlanStepSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    position = serializers.IntegerField()
    title = serializers.CharField()
    explanation = serializers.CharField()
    minutes = serializers.IntegerField()
    step_type = serializers.ChoiceField(choices=StepType.choices)
    resources = PlanResourceSerializer(many=True)


class HiddenResourceSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    url = serializers.URLField()
    kind = serializers.ChoiceField(choices=ResourceKind.choices)
    feedback = FeedbackStateSerializer()


class PlanJobSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    status = serializers.ChoiceField(choices=PlanJob.Status.choices)
    status_display = serializers.CharField()
    error = serializers.CharField(allow_blank=True)
    plan_ready = serializers.BooleanField(help_text="True once the plan can be fetched from the plan endpoint.")
    worker_may_be_stopped = serializers.BooleanField()


class PlanSerializer(serializers.Serializer):
    state = serializers.ChoiceField(choices=PLAN_STATES)
    generated_at = serializers.DateTimeField(allow_null=True)
    source = serializers.CharField(allow_null=True)
    steps = PlanStepSerializer(many=True)
    hidden_resources = HiddenResourceSerializer(many=True, help_text="Resources the user disliked or skipped. They can be undone.")
    quota_remaining = serializers.IntegerField(allow_null=True, help_text="Plan builds left today, or null when unlimited.")
    active_job = PlanJobSerializer(allow_null=True)


# ---------- feedback ----------

class FeedbackActionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=[(a, a) for a in ACTIONS])


class FeedbackResultSerializer(serializers.Serializer):
    resource_id = serializers.IntegerField()
    topic_id = serializers.IntegerField()
    rating = serializers.ChoiceField(choices=Rating.choices, allow_null=True)
    progress = serializers.ChoiceField(choices=Progress.choices, allow_null=True)