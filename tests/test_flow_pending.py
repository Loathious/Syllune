# Pending delivery tests never type into an application or touch the clipboard.
# Fake durable callbacks expose ordering, failures and focus-change races.
import unittest
from unittest.mock import Mock
from syllune.pending_delivery import PendingDelivery


class PendingDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.items = [{'id': 'first', 'text': 'First.'}, {'id': 'second', 'text': 'Second.'}]
        self.update, self.deliver = Mock(return_value=True), Mock(return_value=True)
        self.target = {'target_token': 'edit-1'}
        self.queue = PendingDelivery(lambda: self.items, self.update, self.deliver,
            lambda target: True, lambda: self.target)

    def test_claims_first_item_before_delivery(self):
        order = []
        self.update.side_effect = lambda *a: order.append(a) or True
        self.deliver.side_effect = lambda text: order.append(text) or True
        self.assertTrue(self.queue.insert(self.target))
        self.assertEqual([('first', 'delivering'), 'First.', ('first', 'inserted')], order)

    def test_failed_delivery_returns_item_to_queue(self):
        self.deliver.side_effect = OSError('test failure')
        self.assertFalse(self.queue.insert(self.target))
        self.update.assert_called_with('first', 'pending')

    def test_focus_change_does_not_consume_an_item(self):
        self.queue.current_target = lambda: {'target_token': 'other'}
        self.assertFalse(self.queue.insert(self.target))
        self.update.assert_not_called()

    def test_blocked_target_does_not_type(self):
        self.queue.allowed = lambda target: False
        self.assertFalse(self.queue.insert(self.target))
        self.deliver.assert_not_called()

    def test_failed_durable_claim_does_not_type(self):
        self.update.return_value = False
        self.assertFalse(self.queue.insert(self.target))
        self.deliver.assert_not_called()
