"""저장한 XBRL의 공식 개념·문맥·API 금액을 독립 대조한다.

전 DTS 검증기나 버전 간 의미 대응기가 아니다. 관계는 수집만 하며
기존 수치·차입 구성·비교 결과를 이 모듈의 결과로 승격하지 않는다.
"""
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import PurePosixPath
from urllib.parse import urldefrag
from uuid import uuid4

from lxml import etree

from agent.research_models import FragmentInput
from agent.research_store import encode, now, row_data
from .extraction_service import ACCOUNT_RULES
from .numeric_validation import ACCOUNT_IDS

VERSION = 'xbrl-mapping-1.6'
XI = 'http://www.xbrl.org/2003/instance'
XS = 'http://www.w3.org/2001/XMLSchema'
XL = 'http://www.w3.org/1999/xlink'
LINK = 'http://www.xbrl.org/2003/linkbase'
XD = 'http://xbrl.org/2006/xbrldi'
XSI = 'http://www.w3.org/2001/XMLSchema-instance'
SCOPE_AXIS = 'ConsolidatedAndSeparateFinancialStatementsAxis'
SCOPE_MEMBERS = {'ConsolidatedMember': 'CFS', 'SeparateMember': 'OFS'}


def parse_with_evidence(content):
    """원문은 보존하고 승인된 미사용 작성도구 선언 한 개만 별도 처리한다."""
    import re
    import xml.etree.ElementTree as standard_etree
    if b'<!DOCTYPE' in content.replace(b'\x00', b'').upper() or b'<!ENTITY' in content.replace(b'\x00', b'').upper():
        raise ValueError('unsafe_xbrl_document')
    parser=lambda:etree.XMLParser(resolve_entities=False,no_network=True,load_dtd=False,recover=False,huge_tree=False)
    try:
        return etree.fromstring(content,parser()),None
    except etree.XMLSyntaxError:
        pattern=rb"""\s+xmlns:XBRLConsultingFirm\s*=\s*(["'])""" + '자체작성'.encode() + rb"""\1"""
        matches=list(re.finditer(pattern,content))
        if len(matches)!=1:raise
        match=matches[0]
        copy=content[:match.start()]+content[match.end():]
        if b'XBRLConsultingFirm:' in copy:raise ValueError('vendor_namespace_is_used')
        # 표준 XML 파서의 원문 트리와 엄격 파싱 복사본의 모든 태그/속성/문자열을 대조한다.
        try:original=standard_etree.fromstring(content)
        except standard_etree.ParseError:
            raise ValueError('vendor_namespace_original_xml_invalid') from None
        parsed=etree.fromstring(copy,parser())
        def signature(root):
            return [(n.tag,sorted(n.attrib.items()),n.text,n.tail) for n in root.iter() if isinstance(n.tag,str)]
        before,after=signature(original),signature(parsed)
        if before!=after:raise ValueError('vendor_namespace_content_mismatch')
        return parsed,{'policy':'unused_vendor_namespace_copy-1.0','prefix':'XBRLConsultingFirm',
            'original_uri':'자체작성','declaration_byte_start':match.start(),'declaration_byte_end':match.end(),
            'prefix_unused':True,'strict_copy_parsed':True,'all_expanded_names_attributes_text_match':True,
            'original_tree_sha256':hashlib.sha256(encode(before).encode()).hexdigest(),
            'original_bytes_unchanged':True}


def parse(content):
    return parse_with_evidence(content)[0]


def qname(value, element):
    if not value:
        raise ValueError('missing_qname')
    if ':' in value:
        prefix, local = value.split(':', 1)
        namespace = element.nsmap.get(prefix)
    else:
        namespace, local = element.nsmap.get(None), value
    if not namespace or not local:
        raise ValueError('unbound_qname')
    return '{' + namespace + '}' + local


def proof(source, node):
    return {'source_id': source['id'], 'content_hash': source['content_hash'],
            'xpath': node.getroottree().getpath(node), 'line': node.sourceline}


def number(value):
    try:
        result = Decimal(value)
        return result if result.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def context(node):
    identifiers = node.findall('.//{' + XI + '}identifier')
    periods = node.findall('{' + XI + '}period')
    if len(identifiers) != 1 or len(periods) != 1:
        return {'valid': False, 'reason': 'ambiguous_entity_or_period'}
    entity = identifiers[0]
    p = periods[0]
    instant, start, end = (p.findtext('{' + XI + '}' + x) for x in ('instant', 'startDate', 'endDate'))
    try:
        if instant and not start and not end:
            date.fromisoformat(instant)
            period = {'kind': 'instant', 'as_of': instant}
        elif start and end and not instant and date.fromisoformat(start) <= date.fromisoformat(end):
            period = {'kind': 'duration', 'start': start, 'end': end}
        else:
            raise ValueError
    except ValueError:
        return {'valid': False, 'reason': 'unsupported_period'}
    dimensions = {}
    for member in node.findall('.//{' + XD + '}explicitMember'):
        axis = qname(member.get('dimension'), member)
        if axis in dimensions:
            return {'valid': False, 'reason': 'duplicate_dimension'}
        dimensions[axis] = qname(member.text, member)
    typed = node.findall('.//{' + XD + '}typedMember')
    scope = 'unknown'
    for axis, member in dimensions.items():
        namespace, local = axis[1:].split('}', 1)
        if (local == SCOPE_AXIS and namespace.startswith(('http://xbrl.ifrs.org/taxonomy/',
                                                         'https://xbrl.ifrs.org/taxonomy/'))
                and member.startswith('{' + namespace + '}')):
            scope = SCOPE_MEMBERS.get(etree.QName(member).localname, 'unknown')
    # 부문·계약·은행 등 세부 구분의 값을 본표 합계에 섞지 않는다.
    extra = [axis for axis, member in dimensions.items()
             if not (etree.QName(axis).localname == SCOPE_AXIS
                     and etree.QName(axis).namespace.startswith(('http://xbrl.ifrs.org/taxonomy/','https://xbrl.ifrs.org/taxonomy/'))
                     and member.startswith('{'+etree.QName(axis).namespace+'}')
                     and etree.QName(member).localname in SCOPE_MEMBERS)]
    non_dimensional = [child for container in node.findall('.//{' + XI + '}segment') + node.findall('.//{' + XI + '}scenario')
                       for child in container if child.tag not in {'{' + XD + '}explicitMember', '{' + XD + '}typedMember'}]
    return {'valid': entity.get('scheme') == 'http://dart.fss.or.kr/ifrs/CIK',
            'entity': entity.text, 'entity_scheme': entity.get('scheme'), 'period': period,
            'scope': scope, 'dimensions': dimensions, 'typed_dimension_count': len(typed),
            'non_dimensional_content_count': len(non_dimensional),
            'primary_context': scope != 'unknown' and not extra and not typed and not non_dimensional}


def unit(node):
    measures = node.findall('{' + XI + '}measure')
    if len(measures) != 1 or len(node) != 1:
        return None
    expanded = qname(measures[0].text, measures[0])
    if not expanded.startswith('{http://www.xbrl.org/2003/iso4217}'):
        return None
    return etree.QName(expanded).localname



def borrowing_note_candidate(instance, node, definition, ctx, un, definitions, labels):
    """주석의 원문 수치 후보만 기록한다. 본문 결측값이나 구성 판정을 바꾸지 않는다."""
    if (not definition or definition['type'] != '{' + XI + '}monetaryItemType'
            or not ctx or not ctx.get('valid') or not un or not un.get('currency')):
        return None
    axes = [(axis, member) for axis, member in ctx.get('dimensions', {}).items()
            if definitions.get(axis, {}).get('schema_id') == 'ifrs-full_BorrowingsByNameAxis']
    if len(axes) != 1:
        return None
    axis, member = axes[0]
    member_definition = definitions.get(member)
    nil = node.get('{' + XSI + '}nil') in ('true', '1')
    raw = node.text
    exact = raw if not nil and number(raw) is not None else None
    other_dimensions = {a: v for a, v in ctx['dimensions'].items()
                        if a != axis and etree.QName(a).localname != SCOPE_AXIS}
    return {'status': 'discovery_only', 'usage_scope': 'not_adopted',
        'concept': definition, 'context': ctx, 'unit': un,
        'raw_value': raw, 'exact_value': exact, 'nil': nil,
        'explicit_zero': exact is not None and number(exact) == 0,
        'location': proof(instance, node), 'context_ref': node.get('contextRef'),
        'unit_ref': node.get('unitRef'), 'decimals': node.get('decimals'),
        'borrowing_name_axis': axis, 'borrowing_member': member,
        'member_definition': member_definition, 'member_labels': labels.get(member, []),
        'concept_labels': labels.get(node.tag, []), 'other_dimensions': other_dimensions,
        'typed_dimension_count': ctx.get('typed_dimension_count', 0),
        'not_a_contract_or_legal_entity_identity_verification': True}


def bundle(store, sources, archive_id):
    members = [s for s in sources if s['metadata']['reporting_period'].get('archive_source_id') == archive_id
               and s['metadata']['reporting_period'].get('operation') == 'xbrl_member']
    members.sort(key=lambda s:s['id'])
    parsed_members = {s['id']:parse_with_evidence(store.source_content(s['id'])) for s in members}
    roots = {sid:value[0] for sid,value in parsed_members.items()}
    parser_adjustments = [{'source_id':s['id'],'content_hash':s['content_hash'],**parsed_members[s['id']][1]}
                          for s in members if parsed_members[s['id']][1] is not None]
    instances = [s for s in members if roots[s['id']].tag == '{' + XI + '}xbrl']
    if len(instances) != 1:
        raise ValueError('one_instance_required')
    instance = instances[0]
    root = roots[instance['id']]
    names = {s['metadata']['reporting_period']['member_name']: s for s in members}
    refs = root.findall('{' + LINK + '}schemaRef')
    if len(refs) != 1:
        raise ValueError('one_entity_schema_required')
    schema_name = str(PurePosixPath(instance['metadata']['reporting_period']['member_name']).parent /
                      refs[0].get('{' + XL + '}href', ''))
    entity_schema = names.get(schema_name)
    if not entity_schema:
        raise ValueError('entity_schema_missing')
    schema_root = roots[entity_schema['id']]
    definitions = {}
    href_definitions = {}
    namespace_sources = {}
    missing = []

    def add_schema(source, doc, uri):
        namespace = doc.get('targetNamespace')
        if doc.tag != '{' + XS + '}schema' or not namespace:
            raise ValueError('invalid_schema')
        for node in doc.findall('{' + XS + '}element'):
            if not node.get('name') or not node.get('id'):
                continue
            expanded = '{' + namespace + '}' + node.get('name')
            definition = {'expanded_name': expanded, 'schema_id': node.get('id'), 'namespace': namespace,
                'period_type': node.get('{' + XI + '}periodType'), 'balance': node.get('{' + XI + '}balance'),
                'type': qname(node.get('type'), node) if node.get('type') else None,
                'abstract': node.get('abstract', 'false') in ('true', '1'),
                'substitution_group': qname(node.get('substitutionGroup'), node) if node.get('substitutionGroup') else None,
                'declaration': proof(source, node), 'schema_uri': uri}
            if expanded in definitions:
                raise ValueError('duplicate_concept_declaration')
            definitions[expanded] = definition
            href_definitions[(uri, node.get('id'))] = definition
        namespace_sources[namespace] = source['id']

    add_schema(entity_schema, schema_root, schema_name)
    for node in schema_root.findall('{' + XS + '}import'):
        namespace, uri = node.get('namespace'), node.get('schemaLocation')
        candidates = [s for s in sources if s['metadata']['reporting_period'].get('operation') == 'taxonomy_reference'
                      and s['metadata']['reporting_period'].get('declared_uri') == uri
                      and s['metadata']['reporting_period'].get('referring_source_id') == entity_schema['id']]
        if len(candidates) != 1:
            missing.append({'namespace': namespace, 'declared_uri': uri})
            continue
        doc = parse(store.source_content(candidates[0]['id']))
        if doc.get('targetNamespace') != namespace:
            raise ValueError('schema_namespace_mismatch')
        add_schema(candidates[0], doc, uri)

    def resolve_href(href):
        uri, fragment = urldefrag(href)
        return href_definitions.get((uri, fragment))

    labels = defaultdict(list)
    relationships = []
    for source in members:
        doc = roots[source['id']]
        if doc.tag != '{' + LINK + '}linkbase':
            continue
        for link in doc:
            local = etree.QName(link).localname
            locators = {}
            for loc in link.findall('{' + LINK + '}loc'):
                label = loc.get('{' + XL + '}label')
                if label in locators:
                    raise ValueError('ambiguous_link_locator')
                locators[label] = loc.get('{' + XL + '}href')
            if local == 'labelLink':
                resources = defaultdict(list)
                for resource in link.findall('{' + LINK + '}label'):
                    resources[resource.get('{' + XL + '}label')].append(resource)
                for arc in link.findall('{' + LINK + '}labelArc'):
                    if arc.get('use') == 'prohibited':
                        continue
                    concept = resolve_href(locators.get(arc.get('{' + XL + '}from'), ''))
                    if not concept:
                        continue
                    for resource in resources.get(arc.get('{' + XL + '}to'), []):
                        labels[concept['expanded_name']].append({'text': ''.join(resource.itertext()),
                            'language': resource.get('{http://www.w3.org/XML/1998/namespace}lang'),
                            'role': resource.get('{' + XL + '}role'), 'location': proof(source, resource)})
            elif local in {'presentationLink', 'calculationLink', 'definitionLink'}:
                for arc in link:
                    if not etree.QName(arc).localname.endswith('Arc'):
                        continue
                    origin = locators.get(arc.get('{' + XL + '}from'))
                    target = locators.get(arc.get('{' + XL + '}to'))
                    relationships.append({'kind': local, 'arcrole': arc.get('{' + XL + '}arcrole'),
                        'link_role': link.get('{' + XL + '}role'), 'from_href': origin, 'to_href': target,
                        'from_concept': (resolve_href(origin or '') or {}).get('expanded_name'),
                        'to_concept': (resolve_href(target or '') or {}).get('expanded_name'),
                        'weight': arc.get('weight'), 'order': arc.get('order'), 'priority': arc.get('priority'),
                        'use': arc.get('use'), 'location': proof(source, arc),
                        'status': 'recorded_not_applied'})
    contexts = {}
    for node in root.findall('{' + XI + '}context'):
        key = node.get('id')
        if not key or key in contexts:
            raise ValueError('duplicate_or_missing_context_id')
        contexts[key] = context(node) | {'location': proof(instance, node)}
    units = {}
    for node in root.findall('{' + XI + '}unit'):
        key = node.get('id')
        if not key or key in units:
            raise ValueError('duplicate_or_missing_unit_id')
        units[key] = {'currency': unit(node), 'location': proof(instance, node)}
    facts = []
    note_candidates = []
    target_ids = set().union(*(ACCOUNT_IDS[a] for a in ACCOUNT_RULES))
    for node in root:
        if not node.get('contextRef'):
            continue
        definition = definitions.get(node.tag)
        ctx, un = contexts.get(node.get('contextRef')), units.get(node.get('unitRef'))
        candidate = borrowing_note_candidate(instance,node,definition,ctx,un,definitions,labels)
        if candidate is not None:note_candidates.append(candidate)
        if not definition or definition['schema_id'] not in target_ids:
            continue
        raw = node.text
        nil = node.get('{' + XSI + '}nil') in ('true', '1')
        facts.append({'concept': definition, 'context': ctx, 'unit': un,
            'raw_value': raw, 'exact_value': raw if not nil and number(raw) is not None else None,
            'nil': nil, 'decimals': node.get('decimals'), 'precision': node.get('precision'),
            'context_ref': node.get('contextRef'), 'unit_ref': node.get('unitRef'),
            'location': proof(instance, node), 'labels': labels.get(node.tag, [])})
    return {'receipt': instance['metadata']['receipt_number'], 'instance_source_id': instance['id'],
            'entity_schema_source_id': entity_schema['id'], 'facts': facts,
            'relationships': relationships, 'missing_imports': missing, 'borrowing_note_candidates':note_candidates,
            'namespace_sources': namespace_sources, 'parser_adjustments':parser_adjustments,
            'extension_concepts': [d for d in definitions.values() if d['namespace'] == schema_root.get('targetNamespace')]}


def load_reports(store, sources):
    """공시별 실패를 격리한다. 읽기 실패를 자료 없음이나 검증 성공으로 바꾸지 않는다."""
    reports, failures = {}, []
    archives = [s for s in sources if s['metadata']['reporting_period'].get('operation') == 'xbrl']
    grouped = defaultdict(list)
    for source in archives:
        grouped[source['metadata'].get('receipt_number')].append(source)
    for receipt, candidates in grouped.items():
        if not receipt or len(candidates) != 1:
            failures.append({'receipt_number': receipt, 'source_ids': [s['id'] for s in candidates],
                             'status': 'not_checked', 'reason': 'ambiguous_xbrl_archive'})
            continue
        try:
            report = bundle(store, sources, candidates[0]['id'])
            if report['receipt'] != receipt:
                raise ValueError('archive_instance_receipt_mismatch')
            reports[receipt] = report
        except (ValueError, KeyError, TypeError, etree.XMLSyntaxError):
            failures.append({'receipt_number': receipt, 'source_ids': [candidates[0]['id']],
                             'status': 'not_checked', 'reason': 'stored_xbrl_unreadable_or_invalid'})
    return reports, failures


def bind_record(report, row, company, scope, period, amount, pointer):
    code = row['account_id']
    # 계정 코드 접두사 제거/문자열 유사도/명칭 허용쌍으로 개념을 바꾸지 않는다.
    candidates = [f for f in report['facts'] if f['concept']['schema_id'] == code]
    concept_names = {f['concept']['expanded_name'] for f in candidates}
    def official_namespace(definition):
        ns, code = definition['namespace'], definition['schema_id']
        if code.startswith('ifrs-full_'):
            return ns.startswith(('http://xbrl.ifrs.org/taxonomy/','https://xbrl.ifrs.org/taxonomy/')) and ns.endswith('/ifrs-full')
        if code.startswith('dart_'):
            return ns.startswith('http://dart.fss.or.kr/taxonomy/') and ns.endswith('/ifrs/dart')
        return False
    compatible = all(official_namespace(f['concept']) and not f['concept']['abstract'] and f['concept']['type'] == '{' + XI + '}monetaryItemType'
                     and f['concept']['substitution_group'] == '{' + XI + '}item' for f in candidates)
    concept_status = 'pass' if len(concept_names) == 1 and compatible else 'not_checked'
    selected = [f for f in candidates if f['context'] and f['context']['valid'] and f['context']['primary_context']
                and f['context']['entity'] == company and f['context']['scope'] == scope
                and f['context']['period'] == period and f['unit'] and f['unit']['currency'] == row.get('currency')]
    context_status = 'pass' if selected and concept_status == 'pass' else 'not_checked'
    observed = {number(f['exact_value']) for f in selected if f['exact_value'] is not None}
    missing = any(f['exact_value'] is None for f in selected)
    expected = number(amount)
    if not selected or expected is None or missing:
        amount_status = 'not_checked'
    elif len(observed) != 1:
        amount_status = 'fail'
    else:
        amount_status = 'pass' if observed == {expected} else 'fail'
    periods = {f['concept']['period_type'] for f in selected}
    if selected and periods != {period['kind']}:
        context_status, amount_status = 'fail', 'not_checked'
    return {'official_account_id': code, 'original_account': row.get('account_nm'),
        'scope': scope, 'period': period, 'currency': row.get('currency'), 'api_value': amount,
        'api_location': pointer, 'concept_status': concept_status, 'context_status': context_status,
        'amount_status': amount_status, 'expanded_names': sorted(concept_names),
        'observations': selected, 'candidate_count': len(candidates),
        'comparison_status': 'not_checked', 'composition_status': 'not_checked',
        'usage_scope': ('concept_and_reported_amount_binding_only'
                        if (concept_status,context_status,amount_status) == ('pass','pass','pass')
                        else 'concept_identification_only' if concept_status == 'pass' else 'unavailable')}


def current_period(year, code, statement):
    end = f'{year}-12-31' if code == '11011' else f'{year}-06-30' if code == '11012' else None
    if not end:
        return None
    return {'kind': 'instant', 'as_of': end} if statement == 'BS' else {
        'kind': 'duration', 'start': f'{year}-01-01', 'end': end}



def compare_concepts(current, previous):
    """계정 식별 결과만 연결한다. 금액·구성·기간 증감의 사용 허용과 구분한다."""
    result = {'status': 'not_checked', 'reason': 'concept_or_context_unconfirmed',
              'comparison_use_status': 'not_checked', 'composition_use_status': 'not_checked'}
    if any(r.get('concept_status') != 'pass' or r.get('context_status') != 'pass'
           or not r.get('observations') for r in (current, previous)):
        return result
    a, b = current['observations'][0], previous['observations'][0]
    if (current.get('account') != previous.get('account') or current['scope'] != previous['scope']
            or current['currency'] != previous['currency'] or a['context']['entity'] != b['context']['entity']
            or current['period']['kind'] != previous['period']['kind']):
        return result | {'status': 'fail', 'reason': 'company_account_scope_unit_or_period_type_mismatch'}
    da, db = a['concept'], b['concept']
    if da['expanded_name'] != db['expanded_name']:
        if da['schema_id'] == db['schema_id']:
            return result | {'reason': 'namespace_version_differs_official_bridge_missing'}
        return result | {'status': 'fail', 'reason': 'different_official_concept'}
    if any(da.get(k) != db.get(k) for k in ('type', 'balance', 'period_type', 'abstract', 'substitution_group')):
        return result | {'status': 'fail', 'reason': 'conflicting_concept_definition'}
    return result | {'status': 'pass', 'reason': 'same_expanded_concept_and_definition',
        'current_declaration': da['declaration'], 'previous_declaration': db['declaration']}


class XbrlMappingService:
    def __init__(self, store):
        self.store = store

    def validate_run(self, run_id):
        run = self.store.get_run(run_id)
        with self.store.connection() as conn:
            basis = row_data(self.store._one(conn, 'research_bases', run['basis_id']))
            research = self.store.get_research(basis['research_id'])
            sources = [row_data(s) for s in conn.execute('SELECT * FROM research_sources WHERE run_id=? ORDER BY id', (run_id,))]
        fingerprint = hashlib.sha256(encode({'version': VERSION, 'basis_id': basis['id'],
            'sources': [(s['id'], s['content_hash']) for s in sources]}).encode()).hexdigest()
        with self.store.connection() as conn:
            old = conn.execute('SELECT id FROM research_validation_batches WHERE run_id=? AND fingerprint=?',
                               (run_id, fingerprint)).fetchone()
        if old:
            return self.batch(old[0]) | {'reused': True}
        archives = [s for s in sources if s['metadata']['reporting_period'].get('operation') == 'xbrl']
        reports, report_failures = load_reports(self.store, sources)
        records = []
        excluded_statement_rows = 0
        target_ids = {code: account for account in ACCOUNT_RULES for code in ACCOUNT_IDS[account]}
        for source in sources:
            meta = source['metadata']
            if meta['reporting_period'].get('operation') != 'financials':
                continue
            raw = json.loads(self.store.source_content(source['id']))
            payload = raw.get('response', raw)
            scope = meta['reporting_period'].get('fs_div') or meta['reporting_period'].get('scope')
            for i, row in enumerate(payload['list']):
                if row.get('account_id') not in target_ids:
                    continue
                report = reports.get(row.get('rcept_no'))
                if not report or row.get('corp_code') != research['company']['corp_code']:
                    continue
                account = target_ids[row['account_id']]
                expected_statement = ACCOUNT_RULES[account][0]
                if row['sj_div'] not in ({'CIS','IS'} if expected_statement == 'CIS' else {expected_statement}):
                    excluded_statement_rows += 1
                    continue
                if meta.get('receipt_number') != row.get('rcept_no'):
                    raise ValueError('api_source_receipt_mismatch')
                if raw.get('query') and any(meta['reporting_period'].get(k) != v for k,v in raw['query'].items()):
                    raise ValueError('api_query_metadata_mismatch')
                if date.fromisoformat(row['rcept_no'][:8]) > date.fromisoformat(basis['data']['public_cutoff']):
                    raise ValueError('outside_public_cutoff')
                period = current_period(row['bsns_year'], row['reprt_code'], row['sj_div'])
                if not period:
                    continue
                # 반기 손익의 3개월 값을 누적 값으로 취급하지 않는다.
                field = 'thstrm_add_amount' if row['sj_div'] in ('IS', 'CIS') and row['reprt_code'] != '11011' else 'thstrm_amount'
                pointer = {'source_id': source['id'], 'content_hash': source['content_hash'],
                           'json_pointer': ('/response' if 'response' in raw else '') + f'/list/{i}/{field}'}
                data = bind_record(report, row, research['company']['corp_code'], scope, period, row.get(field), pointer)
                data['account'] = account
                data['receipt_number'] = row['rcept_no']
                if account not in {'short_term_borrowings','current_long_term_borrowings','long_term_borrowings'}:
                    data['composition_status'] = 'not_applicable'
                records.append(data)
        links = []
        for i, current in enumerate(records):
            for previous in records[:i]:
                if (current['account'], current['scope']) != (previous['account'], previous['scope']):continue
                if current['receipt_number'] == previous['receipt_number']:continue
                links.append({'current_api_location':current['api_location'], 'previous_api_location':previous['api_location'],
                    'account':current['account'],'scope':current['scope'],**compare_concepts(current,previous)})
        covered = Counter((r['receipt_number'],r['scope'],r['account']) for r in records)
        missing_coverage = [{'receipt':receipt,'scope':scope,'account':account}
            for receipt in {s['metadata'].get('receipt_number') for s in archives}
            for scope in ('OFS','CFS') for account in ACCOUNT_RULES
            if not covered[(receipt,scope,account)]]
        summary = {'version': VERSION, 'report_count': len(reports), 'record_count': len(records),
            'report_failures': report_failures, 'requested_report_count': len(archives),
            'concept_counts': dict(Counter(r['concept_status'] for r in records)),
            'excluded_statement_rows':excluded_statement_rows,'missing_current_account_coverage':missing_coverage,
            'concept_link_counts':dict(Counter(r['status'] for r in links)),
            'context_counts': dict(Counter(r['context_status'] for r in records)),
            'amount_counts': dict(Counter(r['amount_status'] for r in records)),
            'relationship_count': sum(len(r['relationships']) for r in reports.values()),
            'borrowing_note_candidate_count':sum(len(r['borrowing_note_candidates']) for r in reports.values()),
            'borrowing_note_explicit_zero_candidates':sum(c['explicit_zero'] for r in reports.values() for c in r['borrowing_note_candidates']),
            'extension_concept_count': sum(len(r['extension_concepts']) for r in reports.values()),
            'external_requests': 0, 'model_calls': 0, 'cross_version_mapping_completed': False,
            'stage04_completed': False, 'full_xbrl_validation_completed': False}
        fragments = []
        for report in reports.values():
            fragment = self.store.save_fragment(report['instance_source_id'], FragmentInput(
                text='XBRL 구조·문맥·관계의 로컬 추출 기록', context={'kind': 'xbrl_bundle',
                    'version': VERSION, **report}, location={'kind': 'xml_root', 'xpath': '/*'}))
            fragments.append(fragment['id'])
        stamp, batch_id = now(), uuid4().hex
        with self.store.connection(write=True) as conn:
            old = conn.execute('SELECT id FROM research_validation_batches WHERE run_id=? AND fingerprint=?',
                               (run_id,fingerprint)).fetchone()
            if old:return self.batch(old[0]) | {'reused':True}
            conn.execute('INSERT INTO research_validation_batches VALUES (?,?,?,?,?,?,?)',
                (batch_id, run_id, fingerprint, VERSION, encode({'fragment_ids': fragments, 'concept_links': links}), encode(summary), stamp))
            for record in records:
                conn.execute('INSERT INTO research_validations VALUES (?,?,?,?,?,?,?)',
                    (uuid4().hex, batch_id, None, 'XBRL_STANDARD_CONCEPT', record['concept_status'], encode(record), stamp))
            self.store._event(conn,basis['research_id'],basis['id'],run_id,'xbrl_mapping_completed',
                              {'batch_id':batch_id,'policy_version':VERSION,**summary})
        return self.batch(batch_id) | {'reused': False}

    def batch(self, batch_id):
        with self.store.connection() as conn:
            result = row_data(self.store._one(conn, 'research_validation_batches', batch_id))
            result['validations'] = [row_data(v) for v in conn.execute(
                'SELECT * FROM research_validations WHERE batch_id=? ORDER BY rowid', (batch_id,))]
        return result
