from django.test import SimpleTestCase, TestCase

from apps.feedback.models import Progress, Rating
from apps.feedback.selectors import feedback_for, hidden_feedback_for, ranking_signals
from apps.feedback.services import set_feedback
from services import ranking
from services.ranking import Signals

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


class RankingSignalsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = make_user("alice@example.com")
        cls.bob = make_user("bob@example.com")
        cls.carol = make_user("carol@example.com")
        cls.dave = make_user("dave@example.com")
        topic = make_topic()
        cls.r1 = make_resource(topic, "video", "https://example.com/1")
        cls.r2 = make_resource(topic, "book", "https://example.com/2")
        set_feedback(user=cls.alice, resource=cls.r1, action="like")
        set_feedback(user=cls.bob, resource=cls.r1, action="like")
        set_feedback(user=cls.carol, resource=cls.r1, action="dislike")
        set_feedback(user=cls.bob, resource=cls.r2, action="done")

    def test_only_my_own_feedback_is_returned(self):
        result = ranking_signals(self.alice, [self.r1, self.r2])
        self.assertEqual(result[self.r1.pk], Signals(rating="like"))
        self.assertEqual(result[self.r2.pk], Signals())

    def test_other_learners_feedback_is_invisible(self):
        # Bob liked r1, Carol disliked it, Bob marked r2 done: none of it reaches Dave.
        self.assertEqual(
            ranking_signals(self.dave, [self.r1, self.r2]),
            {self.r1.pk: Signals(), self.r2.pk: Signals()},
        )

    def test_changing_someone_elses_feedback_never_changes_mine(self):
        before = ranking_signals(self.alice, [self.r1, self.r2])
        set_feedback(user=self.bob, resource=self.r1, action="dislike")
        set_feedback(user=self.carol, resource=self.r2, action="like")
        self.assertEqual(ranking_signals(self.alice, [self.r1, self.r2]), before)

    def test_progress_is_never_counted_as_a_rating(self):
        self.assertEqual(ranking_signals(self.bob, [self.r2])[self.r2.pk], Signals(progress="done"))

    def test_only_the_requested_resources_come_back(self):
        self.assertEqual(set(ranking_signals(self.dave, [self.r1])), {self.r1.pk})

    def test_one_query_however_many_resources(self):  # N+1 guard
        with self.assertNumQueries(1):
            ranking_signals(self.dave, [self.r1, self.r2])

    def test_empty_input_makes_no_query(self):
        with self.assertNumQueries(0):
            self.assertEqual(ranking_signals(self.dave, []), {})


class HiddenFeedbackTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = make_user("alice@example.com")
        cls.bob = make_user("bob@example.com")
        cls.topic = make_topic()
        cls.other_topic = make_topic("Rust")
        cls.disliked = make_resource(cls.topic, "video", "https://example.com/1")
        cls.skipped = make_resource(cls.topic, "book", "https://example.com/2")
        cls.liked = make_resource(cls.topic, "article", "https://example.com/3")
        cls.done = make_resource(cls.topic, "exercise", "https://example.com/4")
        cls.other_topic_resource = make_resource(cls.other_topic, "video", "https://example.com/5")
        set_feedback(user=cls.alice, resource=cls.disliked, action="dislike")
        set_feedback(user=cls.alice, resource=cls.skipped, action="skip")
        set_feedback(user=cls.alice, resource=cls.liked, action="like")
        set_feedback(user=cls.alice, resource=cls.done, action="done")
        set_feedback(user=cls.alice, resource=cls.other_topic_resource, action="dislike")
        set_feedback(user=cls.bob, resource=cls.disliked, action="dislike")

    def test_only_disliked_and_skipped_resources_of_this_topic(self):
        rows = hidden_feedback_for(self.alice, self.topic)
        self.assertEqual({row.resource_id for row in rows}, {self.disliked.pk, self.skipped.pk})

    def test_another_users_feedback_is_never_included(self):
        self.assertEqual(list(hidden_feedback_for(self.bob, self.topic))[0].resource_id, self.disliked.pk)
        self.assertEqual(len(hidden_feedback_for(self.bob, self.topic)), 1)

    def test_other_topics_are_excluded(self):
        ids = {row.resource_id for row in hidden_feedback_for(self.alice, self.topic)}
        self.assertNotIn(self.other_topic_resource.pk, ids)

    def test_the_resource_is_loaded_in_the_same_query(self):
        with self.assertNumQueries(1):
            rows = list(hidden_feedback_for(self.alice, self.topic))
            for row in rows:
                row.resource.title


class VocabularyTests(SimpleTestCase):
    def test_ranking_uses_the_same_words_as_the_models(self):
        self.assertEqual(ranking.LIKE, Rating.LIKE.value)
        self.assertEqual(ranking.DISLIKE, Rating.DISLIKE.value)
        self.assertEqual(ranking.DONE, Progress.DONE.value)
        self.assertEqual(ranking.SKIPPED, Progress.SKIPPED.value)