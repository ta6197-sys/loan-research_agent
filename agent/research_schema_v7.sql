-- 원문 조각 검색의 source_id 조건을 가속한다. 기존 조각과 근거는 변경하지 않는다.
CREATE INDEX research_fragment_source_lookup ON research_fragments(source_id);
