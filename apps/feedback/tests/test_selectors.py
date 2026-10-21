from django.test import TestCase

from apps.feedback.selectors import feedback_for
from apps.feedback.services import set_feedback

from .helpers import make_resource, make_topic, make_user


class FeedbackForTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = make_user("alice@example.com")
        cls.bob = make_user("bob@example.com")
        topic = make_topic()
        cls.r1 = make_resource(topic, "video", "https://example.com/1")
        cls.r2 = make_resource(topic, "book", "https://example.com/2")

    def test_only_the_users_own_feedback_is_returned(self):
        set_feedback(user=self.alice, resource=self.r1, action="like")
        set_feedback(user=self.bob, resource=self.r2, action="dislike")
        result = feedback_for(self.alice, [self.r1, self.r2])
        self.assertEqual(set(result), {self.r1.pk})
        self.assertEqual(result[self.r1.pk].rating, "like")

    def test_one_query_for_many_resources(self):  # N+1 guard
        set_feedback(user=self.alice, resource=self.r1, action="like")
        with self.assertNumQueries(1):
            feedback_for(self.alice, [self.r1, self.r2])

    def test_empty_input_makes_no_query(self):
        with self.assertNumQueries(0):
            self.assertEqual(feedback_for(self.alice, []), {})