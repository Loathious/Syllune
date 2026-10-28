# Release configuration must never update Syllune from a different product.
# Packaging and a source checkout share their name/version without touching data.

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from whisper_key import brand, update_check


class DistributionTests(unittest.TestCase):
    def test_updates_point_to_branded_repository(self):
        with patch.object(brand, 'repository', return_value='example/syllune'):
            self.assertIn('example/syllune', update_check._how_to_update())
            self.assertNotIn('pip install', update_check._how_to_update())

    def test_unconfigured_updates_never_contact_upstream(self):
        with patch.object(brand, 'repository', return_value=''), patch('threading.Thread') as thread:
            update_check.maybe_check_for_update(lambda message: None, {'enabled': True})
        thread.assert_not_called()

    def test_frozen_version_does_not_need_source_pyproject(self):
        from whisper_key.utils import get_version
        with patch('sys.frozen', True, create=True):
            self.assertEqual(brand.VERSION, get_version())
