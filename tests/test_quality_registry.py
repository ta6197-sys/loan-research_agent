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

    def test_v14_to_v22_match_original_design_and_do_not_claim_execution(self):
        meanings = {'V14': '사실 주장', 'V15': '원인·위험', 'V16': '공개 자료',
                    'V17': '기본 항목', 'V18': '빈 값과 0', 'V19': '본문·표·차트·출력',
                    'V20': '후속 자료', 'V21': '외부 전송', 'V22': '한도 도달'}
        for rule, text in meanings.items():
            with self.subTest(rule=rule):
                item = get(rule)
                self.assertIn(text, item['description'])
                self.assertEqual('quality-registry-1.1', item['registry_version'])
                if rule != 'V18':
                    self.assertEqual('partially_implemented', item['implementation_state'])
