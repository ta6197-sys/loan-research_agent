"""사용자가 승인한 네 가지 정확한 계정 명칭 조합만 근거와 함께 연결한다."""
from .document_extractor import normalize_label
from .source_locations import raw_text

VERSION='account-equivalence-1.0'
PAIRS={
 'inventory':({'유동재고자산','재고자산'},'ifrs-full_Inventories','유동자산'),
 'receivables':({'매출채권및기타유동채권','매출채권및기타채권'},'ifrs-full_TradeAndOtherCurrentReceivables','유동자산'),
 'short_term_borrowings':({'유동차입금','단기차입금'},'ifrs-full_ShorttermBorrowings','유동부채'),
 'current_long_term_borrowings':({'비유동차입금의유동성대체부분','유동성장기부채'},'ifrs-full_CurrentPortionOfLongtermBorrowings','유동부채'),
}


def prepare_context(fact,validation,content,raw_check):
    d=fact['data'];account=d['account'];rule=PAIRS.get(account)
    if not rule or validation['status']!='pass':return None
    section=fact.get('fragment',{}).get('data',{}).get('context',{}).get('row_section')
    if not section or not raw_check(content,section.get('location')):return None
    location=section['location']
    original_section=raw_text(content[location['byte_start']:location['byte_end']])
    names,official,classification=rule
    if normalize_label(d['original_account']) not in names:return None
    if normalize_label(original_section)!=classification or validation['data'].get('account_id')!=official:return None
    check=validation['data'].get('checks',{}).get('V12')
    if account in {'short_term_borrowings','current_long_term_borrowings'} and (not check or check.get('status')!='pass'):return None
    return {'version':VERSION,'fact_id':fact['id'],'numeric_validation_id':validation['id'],
            'account':account,'official_account_id':official,'original_account':d['original_account'],
            'classification':classification,'classification_evidence':{'text':original_section,'normalized':classification,'location':location},
            'scope':d['scope'],'currency':d['currency'],'period':d['period'],
            'composition_check':check if account.endswith('borrowings') else None}


def verified_alias_pair(current,previous):
    if not current or not previous:return None
    account=current.get('account');rule=PAIRS.get(account)
    if not rule or previous.get('account')!=account:return None
    names,official,classification=rule
    if {normalize_label(current.get('original_account','')),normalize_label(previous.get('original_account',''))}!=names:return None
    if any(x.get('official_account_id')!=official or x.get('classification')!=classification
           or not x.get('classification_evidence') or not x.get('fact_id') or not x.get('numeric_validation_id') for x in (current,previous)):return None
    if any(current.get(k)!=previous.get(k) for k in ('scope','currency')):return None
    if account in {'short_term_borrowings','current_long_term_borrowings'}:
        if any(x.get('composition_check',{}).get('status')!='pass' or not x['composition_check'].get('composition_validation_id') for x in (current,previous)):return None
    return {'version':VERSION,'basis':'exact_reviewed_name_pair_official_code_classification_and_required_composition',
            'account':account,'current':current,'previous':previous}
