# Pending delivery tests never type into an application or touch the clipboard.
# Fake durable callbacks expose ordering, failures and which dictation is offered.
import unittest
from unittest.mock import Mock, call
from syllune.pending_delivery import PendingDelivery


class PendingDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.items = [{'id': 'first', 'text': 'First.'}, {'id': 'second', 'text': 'Second.'}]
        self.update, self.deliver = Mock(return_value=True), Mock(return_value=True)
        self.changed = Mock()
        self.target = {'exe': 'notepad.exe'}
        self.queue = PendingDelivery(lambda: self.items, self.update, self.deliver,
            lambda target: True, self.changed)

    def test_the_newest_dictation_is_the_one_offered(self):
        self.assertEqual('second', self.queue.latest()['id'])
        self.items = []
        self.assertIsNone(self.queue.latest())

    def test_claims_the_newest_item_before_delivery_and_retires_older_ones(self):
        order = []
        self.update.side_effect = lambda *a: order.append(a) or True
        self.deliver.side_effect = lambda text: order.append(text) or True
        self.assertTrue(self.queue.insert(self.target))
        self.assertEqual([('second', 'delivering'), ('first', 'failed'), 'Second.',
                          ('second', 'inserted')], order)

    def test_failed_delivery_returns_item_to_queue(self):
        self.deliver.side_effect = OSError('test failure')
        self.assertFalse(self.queue.insert(self.target))
        self.update.assert_called_with('second', 'pending')

    def test_nothing_is_typed_without_a_window_to_type_into(self):
        self.assertFalse(self.queue.insert(None))
        self.assertFalse(self.queue.insert({}))
        self.update.assert_not_called()

    def test_blocked_target_does_not_type(self):
        self.queue.allowed = lambda target: False
        self.assertFalse(self.queue.insert(self.target))
        self.deliver.assert_not_called()

    def test_failed_durable_claim_does_not_type(self):
        self.update.return_value = False
        self.assertFalse(self.queue.insert(self.target))
        self.deliver.assert_not_called()

    def test_pasting_the_same_text_another_way_settles_it(self):
        self.assertFalse(self.queue.settle('Something else.'))
        self.update.assert_not_called()
        self.assertTrue(self.queue.settle('Second.'))
        self.assertEqual([call('first', 'failed'), call('second', 'inserted')], self.update.call_args_list)
        self.deliver.assert_not_called()

    def test_retiring_keeps_everything_in_history_only(self):
        self.assertEqual(2, self.queue.retire_all())
        self.assertEqual([call('first', 'failed'), call('second', 'failed')], self.update.call_args_list)
        self.changed.assert_called_once()
