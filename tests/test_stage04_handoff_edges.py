"""인계된 파서 제한·공시별 실패 격리·공식 기준자료 보존을 검사한다."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lxml import etree

from agent.finance.xbrl_mapping import load_reports,parse_with_evidence
from agent.research_models import SourceInput
from agent.research_store import ResearchStore
from tests.test_research_storage import research_input
from sh.collect_official_reference import public_reference_url


class MetadataParserTests(unittest.TestCase):
    def test_only_unused_exact_vendor_declaration_is_removed_from_copy(self):
        raw='<root xmlns:XBRLConsultingFirm="자체작성"><a value="보존">내용</a>꼬리</root>'.encode()
        before=bytes(raw)
        node,evidence=parse_with_evidence(raw)
        self.assertEqual('내용',node[0].text);self.assertEqual('꼬리',node[0].tail)
        self.assertTrue(evidence['all_expanded_names_attributes_text_match'])
        self.assertEqual(raw,before);self.assertEqual(64,len(evidence['original_tree_sha256']))

    def test_used_prefix_duplicate_declaration_other_syntax_and_unknown_vendor_are_rejected(self):
        for text in (
            '<root xmlns:XBRLConsultingFirm="자체작성"><XBRLConsultingFirm:a/></root>',
            '<root xmlns:XBRLConsultingFirm="자체작성" XBRLConsultingFirm:a="x"/>',
            '<root xmlns:XBRLConsultingFirm="자체작성" xmlns:XBRLConsultingFirm="자체작성"/>',
            '<root xmlns:XBRLConsultingFirm="자체작성"><a></root>',
            '<root xmlns:Other="자체작성"/>',
        ):
            with self.subTest(text=text), self.assertRaises((ValueError,etree.XMLSyntaxError)):
                parse_with_evidence(text.encode())

    def test_logical_content_difference_is_not_accepted(self):
        raw='<root xmlns:XBRLConsultingFirm="자체작성"><a>원문</a></root>'.encode()
        import xml.etree.ElementTree as standard
        fake=standard.fromstring('<root><a>다른내용</a></root>')
        with patch.object(standard,'fromstring',return_value=fake):
            with self.assertRaisesRegex(ValueError,'content_mismatch'):parse_with_evidence(raw)


class FailureIsolationTests(unittest.TestCase):
    def source(self,identifier,receipt):
        return {'id':identifier,'metadata':{'receipt_number':receipt,'reporting_period':{'operation':'xbrl'}}}

    def test_bad_archive_keeps_other_report_and_explicit_failure(self):
        sources=[self.source('a','one'),self.source('b','two')]
        def read(store,sources,identifier):
            if identifier=='a':raise etree.XMLSyntaxError('synthetic secret',0,0,0)
            return {'receipt':'two','facts':[]}
        with patch('agent.finance.xbrl_mapping.bundle',side_effect=read):
            reports,failures=load_reports(None,sources)
        self.assertEqual({'two'},set(reports));self.assertEqual('one',failures[0]['receipt_number'])
        self.assertNotIn('synthetic secret',str(failures))

    def test_duplicate_receipt_does_not_silently_choose_one_archive(self):
        sources=[self.source('a','same'),self.source('b','same')]
        with patch('agent.finance.xbrl_mapping.bundle') as read:
            reports,failures=load_reports(None,sources)
        self.assertEqual({},reports);self.assertEqual('ambiguous_xbrl_archive',failures[0]['reason'])
        read.assert_not_called()


class OfficialReferenceTests(unittest.TestCase):
    def test_authentication_redirect_and_credential_query_are_not_public_references(self):
        self.assertTrue(public_reference_url('https://www.ifrs.org/issued-standards/ifrs-taxonomy/'))
        self.assertTrue(public_reference_url('https://www.ifrs.org/issued-standards/ifrs-accounting-standards/'))
        for url in ('https://www.ifrs.org/login?returnUrl=private','https://www.ifrs.org/a?token=private',
                    'https://attacker.test/data','http://www.ifrs.org/public','https://user:pass@www.ifrs.org/public'):
            self.assertFalse(public_reference_url(url))

    def test_standard_rights_and_source_recovery_are_preserved_separately_from_company_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=ResearchStore(root/'db.sqlite',root/'sources')
            research=store.create_research(research_input());run=research['runs'][0]['id']
            meta=SourceInput(kind='official_standard',title='합성 기준 v1',publisher='합성 발행기관',
                retrieved_at='2026-10-21',rights_status='public_reference',usage_restrictions=['재배포 금지'],
                reporting_period={'operation':'official_reference','version':'v1'})
            before=meta.model_dump()
            with patch.object(store,'_finish_source_write',side_effect=RuntimeError('synthetic crash')):
                with self.assertRaises(RuntimeError):store.save_source(run,meta,b'public reference only')
            reopened=ResearchStore(root/'db.sqlite',root/'sources')
            self.assertEqual(1,len(reopened.recovery_summary['recovered']))
            sid=reopened.recovery_summary['recovered'][0];source=reopened.get_source(sid)
            self.assertEqual(before,source['metadata']);self.assertEqual(b'public reference only',reopened.source_content(sid))
            with reopened.connection() as conn:
                self.assertEqual(0,conn.execute('SELECT COUNT(*) FROM research_facts').fetchone()[0])
            wrong=deepcopy(before);wrong['rights_status']='public_disclosure'
            with self.assertRaises(ValueError):reopened.save_source(run,SourceInput(**wrong),b'ref')
