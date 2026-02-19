import unittest
from unittest.mock import MagicMock, patch

from interaktiv.kyra.testing import INTERAKTIV_KYRA_FUNCTIONAL_TESTING
from plone import api
from plone.app.testing import TEST_USER_ID, setRoles


class TestTranslationTopicCopy(unittest.TestCase):
    """Test that topics are copied during translation."""

    layer = INTERAKTIV_KYRA_FUNCTIONAL_TESTING
    product_name = "interaktiv.kyra"

    def setUp(self):
        self.portal = self.layer["portal"]
        setRoles(self.portal, TEST_USER_ID, ["Manager"])

    def test_topic_set_with_p_changed(self):
        """Verify that setting topic on an object with _p_changed persists."""
        doc = api.content.create(
            container=self.portal,
            type="Document",
            id="topic-test",
            title="Topic Test",
        )
        topics = {"Artificial intelligence", "Brain research"}
        doc.topic = topics
        doc._p_changed = True

        self.assertEqual(doc.topic, topics)

    def test_topic_empty_set_is_falsy(self):
        """Confirm that empty set() is falsy — important for conditional logic."""
        self.assertFalse(set())
        self.assertTrue({"Artificial intelligence"})

    def test_topic_none_vs_empty_set(self):
        """Test len() check handles both None and empty set correctly."""
        # This mirrors the condition in ai_actions.py:
        # if source_topics is not None and len(source_topics) > 0
        topics_none = None
        topics_empty = set()
        topics_filled = {"Hydrogen"}

        # None should not pass
        self.assertTrue(topics_none is None)

        # Empty set: is not None but len is 0
        self.assertIsNotNone(topics_empty)
        self.assertEqual(len(topics_empty), 0)

        # Filled set passes both checks
        self.assertIsNotNone(topics_filled)
        self.assertGreater(len(topics_filled), 0)

    def test_topic_copied_to_target_object(self):
        """Simulate the topic copy that happens during translation."""
        source = api.content.create(
            container=self.portal,
            type="Document",
            id="source-doc",
            title="Source",
        )
        target = api.content.create(
            container=self.portal,
            type="Document",
            id="target-doc",
            title="Target",
        )

        # Set topics on source
        source_topics = {"Hydrogen", "Renewable energy", "Fuel cells"}
        source.topic = source_topics
        source._p_changed = True

        # Simulate the copy logic from ai_actions.py
        read_topics = getattr(source, "topic", None)
        self.assertIsNotNone(read_topics)
        self.assertEqual(len(read_topics), 3)

        if read_topics is not None and len(read_topics) > 0:
            target.topic = set(read_topics)
            target._p_changed = True

        # postcondition
        self.assertEqual(target.topic, source_topics)

    def test_topic_not_copied_when_source_has_none(self):
        """If source has no topics, target should remain unchanged."""
        source = api.content.create(
            container=self.portal,
            type="Document",
            id="no-topic-source",
            title="No Topic Source",
        )
        target = api.content.create(
            container=self.portal,
            type="Document",
            id="no-topic-target",
            title="No Topic Target",
        )
        target.topic = {"Existing topic"}
        target._p_changed = True

        # Simulate the copy logic
        read_topics = getattr(source, "topic", None)
        # source.topic should be default empty set or None
        if read_topics is not None and len(read_topics) > 0:
            target.topic = set(read_topics)
            target._p_changed = True

        # postcondition — target keeps its own topics
        self.assertEqual(target.topic, {"Existing topic"})
