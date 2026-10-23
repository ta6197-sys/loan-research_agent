import unittest

from agent.finance.quality_registry import DIMENSIONS, all_rules, get, is_mandatory


class QualityRegistryTests(unittest.TestCase):
    def test_v01_to_v22_have_a_stable_metadata_contract(self):
        rules = {item['rule_id']: item for item in all_rules()}
        self.assertEqual({'V%02d' % value for value in range(1, 23)}, set(rules))
        for item in rules.values():
            self.assertIn(item['quality_dimension'], DIMENSIONS)
            self.assertIn(item['implementation_state'], {'implemented', 'partially_implemented', 'design_only', 'not_applicable'})
            self.assertTrue(item['registry_version'])

    def test_mandatory_checks_keep_existing_acceptance_definition(self):
        self.assertTrue(is_mandatory('V01'))
        self.assertTrue(is_mandatory('V18'))
        self.assertFalse(is_mandatory('V12'))
        self.assertEqual('V12_note_amounts', get('V12_note_amounts')['rule_id'])
        self.assertEqual('completeness', get('V12_note_amounts')['quality_dimension'])
