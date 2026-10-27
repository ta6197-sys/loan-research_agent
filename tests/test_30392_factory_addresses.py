import unittest

from sh.audit_30392_factory_addresses_2025 import comparable_address


class FactoryAddressTests(unittest.TestCase):
    def test_parenthetical_location_does_not_break_exact_road_address(self):
        legal = '대구광역시 달성군 논공읍 논공로 438 (본리리, 산도브레이크(주))'
        factory = '대구광역시 달성군 논공읍 논공로 438'
        self.assertEqual(comparable_address(legal), comparable_address(factory))

    def test_distinct_street_number_is_not_equal(self):
        self.assertNotEqual(comparable_address('경상남도 김해시 서김해산단로 20-17'),
                            comparable_address('경상남도 김해시 서김해산단로 20-18'))


if __name__ == '__main__':
    unittest.main()
