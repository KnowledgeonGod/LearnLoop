from django.test import TestCase

from apps.topics.selectors import popular_topics, recent_topics_for

from .helpers import make_user, search_at


class RecentTopicsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = make_user("alice@example.com")
        cls.bob = make_user("bob@example.com")

    def test_newest_first_and_only_own(self):
        search_at(self.alice, "Python", 0)
        search_at(self.alice, "Rust", 1)
        search_at(self.bob, "Secret", 2)
        names = [i.topic.name for i in recent_topics_for(self.alice)]
        self.assertEqual(names, ["Rust", "Python"])

    def test_limit_is_respected(self):
        for i in range(5):
            search_at(self.alice, f"Topic {i}", i)
        self.assertEqual(len(recent_topics_for(self.alice, limit=3)), 3)

    def test_one_query_regardless_of_row_count(self):  # N+1 guard
        for i in range(3):
            search_at(self.alice, f"Topic {i}", i)
        with self.assertNumQueries(1):
            [item.topic.name for item in recent_topics_for(self.alice)]


class PopularTopicsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = make_user("alice@example.com")
        cls.bob = make_user("bob@example.com")
        cls.carol = make_user("carol@example.com")

    def test_hidden_until_enough_different_users_searched(self):  # privacy
        search_at(self.alice, "Python", 0)
        search_at(self.alice, "Python", 1)  # same user twice still counts once
        self.assertEqual(popular_topics(), [])

    def test_ranked_by_distinct_searchers(self):
        for user in (self.alice, self.bob, self.carol):
            search_at(user, "Python")
        for user in (self.alice, self.bob):
            search_at(user, "Rust")
        self.assertEqual([t.name for t in popular_topics()], ["Python", "Rust"])

    def test_one_query(self):
        for user in (self.alice, self.bob):
            search_at(user, "Python")
        with self.assertNumQueries(1):
            [t.name for t in popular_topics()]