import unittest
from agent.finance.quality_dictionary import entries, get


class QualityDictionaryTests(unittest.TestCase):
    def test_dictionary_reuses_existing_official_ids_and_aliases(self):
        revenue = get('revenue')
        self.assertIn('ifrs-full_Revenue', revenue['official_account_ids'])
        self.assertIn('매출액', revenue['aliases'])
        self.assertEqual('duration', revenue['expected_period_kind'])
        self.assertEqual(len(entries()), len({item['internal_account'] for item in entries()}))
