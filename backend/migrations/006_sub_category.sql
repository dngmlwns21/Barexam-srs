-- Migration 006: Add sub_category to questions for detailed card classification
-- DB subjects are: 공법, 민사법, 형사법, 법조윤리
-- Safe to re-run: uses IF NOT EXISTS / IS NULL guards

ALTER TABLE questions ADD COLUMN IF NOT EXISTS sub_category TEXT;
CREATE INDEX IF NOT EXISTS idx_questions_sub_category ON questions(sub_category);

-- ════════════════════════════════════════════════════════════════
-- 형사법 → 형법총론 / 형법각론 / 특별형법 / 형사소송법
-- ════════════════════════════════════════════════════════════════

-- 형사소송법 (먼저: 절차법 키워드가 명확)
UPDATE questions SET sub_category = '형사소송법'
FROM subjects s
WHERE questions.subject_id = s.id AND s.name = '형사법'
  AND (  questions.stem ILIKE '%수사%' OR questions.stem ILIKE '%공소%'
      OR questions.stem ILIKE '%증거능력%' OR questions.stem ILIKE '%전문증거%'
      OR questions.stem ILIKE '%전문법칙%' OR questions.stem ILIKE '%위법수집증거%'
      OR questions.stem ILIKE '%공판%' OR questions.stem ILIKE '%자백의 증거%'
      OR questions.stem ILIKE '%피고인%' OR questions.stem ILIKE '%피의자%'
      OR questions.stem ILIKE '%체포%' OR questions.stem ILIKE '%구속%'
      OR questions.stem ILIKE '%압수%' OR questions.stem ILIKE '%수색%'
      OR questions.stem ILIKE '%불기소%' OR questions.stem ILIKE '%공소제기%'
      OR questions.stem ILIKE '%공소시효%' OR questions.stem ILIKE '%공소장%'
      OR questions.stem ILIKE '%형사재판%' OR questions.stem ILIKE '%항소심%'
      OR questions.stem ILIKE '%상고심%' OR questions.stem ILIKE '%재심%'
      OR questions.stem ILIKE '%비상상고%' OR questions.stem ILIKE '%형사집행%'
      OR questions.stem ILIKE '%진술거부권%' OR questions.stem ILIKE '%영장%'
      OR questions.stem ILIKE '%증인신문%' OR questions.stem ILIKE '%감정%'
      OR questions.stem ILIKE '%피해자 진술%'
  )
  AND questions.sub_category IS NULL;

-- 특별형법
UPDATE questions SET sub_category = '특별형법'
FROM subjects s
WHERE questions.subject_id = s.id AND s.name = '형사법'
  AND (  questions.stem ILIKE '%성폭력%' OR questions.stem ILIKE '%성범죄%'
      OR questions.stem ILIKE '%아동청소년%' OR questions.stem ILIKE '%아동·청소년%'
      OR questions.stem ILIKE '%마약%' OR questions.stem ILIKE '%특정경제범죄%'
      OR questions.stem ILIKE '%폭력행위 등 처벌%' OR questions.stem ILIKE '%특가법%'
      OR questions.stem ILIKE '%도로교통법%' OR questions.stem ILIKE '%교통사고처리%'
      OR questions.stem ILIKE '%특별형법%'
  )
  AND questions.sub_category IS NULL;

-- 형법총론
UPDATE questions SET sub_category = '형법총론'
FROM subjects s
WHERE questions.subject_id = s.id AND s.name = '형사법'
  AND (  questions.stem ILIKE '%구성요건%' OR questions.stem ILIKE '%위법성%'
      OR questions.stem ILIKE '%책임능력%' OR questions.stem ILIKE '%공범%'
      OR questions.stem ILIKE '%교사범%' OR questions.stem ILIKE '%종범%'
      OR questions.stem ILIKE '%방조%' OR questions.stem ILIKE '%미수%'
      OR questions.stem ILIKE '%중지미수%' OR questions.stem ILIKE '%불능미수%'
      OR questions.stem ILIKE '%장애미수%' OR questions.stem ILIKE '%죄수%'
      OR questions.stem ILIKE '%일죄%' OR questions.stem ILIKE '%경합범%'
      OR questions.stem ILIKE '%상상적 경합%' OR questions.stem ILIKE '%죄형법정주의%'
      OR questions.stem ILIKE '%고의%' OR questions.stem ILIKE '%과실%'
      OR questions.stem ILIKE '%결과적 가중범%' OR questions.stem ILIKE '%인과관계%'
      OR questions.stem ILIKE '%객관적 귀속%' OR questions.stem ILIKE '%위법성조각%'
      OR questions.stem ILIKE '%정당행위%' OR questions.stem ILIKE '%정당방위%'
      OR questions.stem ILIKE '%긴급피난%' OR questions.stem ILIKE '%자구행위%'
      OR questions.stem ILIKE '%피해자의 승낙%' OR questions.stem ILIKE '%착오%'
      OR questions.stem ILIKE '%원인에서 자유로운 행위%' OR questions.stem ILIKE '%형사미성년자%'
      OR questions.stem ILIKE '%누범%' OR questions.stem ILIKE '%집행유예%'
      OR questions.stem ILIKE '%선고유예%' OR questions.stem ILIKE '%가석방%'
      OR questions.stem ILIKE '%소추조건%' OR questions.stem ILIKE '%친고죄%'
      OR questions.stem ILIKE '%반의사불벌죄%'
  )
  AND questions.sub_category IS NULL;

-- 나머지 형사법 → 형법각론
UPDATE questions SET sub_category = '형법각론'
FROM subjects s
WHERE questions.subject_id = s.id AND s.name = '형사법'
  AND questions.sub_category IS NULL;

-- ════════════════════════════════════════════════════════════════
-- 민사법 → 가족법 / 물권법 / 채권각론 / 채권총론 / 상법 / 민사소송법 / 민법총론
-- ════════════════════════════════════════════════════════════════

UPDATE questions SET sub_category = '민사소송법'
FROM subjects s
WHERE questions.subject_id = s.id AND s.name = '민사법'
  AND (  questions.stem ILIKE '%민사소송%' OR questions.stem ILIKE '%강제집행%'
      OR questions.stem ILIKE '%가압류%' OR questions.stem ILIKE '%가처분%'
      OR questions.stem ILIKE '%소의 이익%' OR questions.stem ILIKE '%소송물%'
      OR questions.stem ILIKE '%변론%' OR questions.stem ILIKE '%증거조사%'
      OR questions.stem ILIKE '%판결의 확정%' OR questions.stem ILIKE '%기판력%'
      OR questions.stem ILIKE '%항소%' OR questions.stem ILIKE '%상고%'
      OR questions.stem ILIKE '%재심%' OR questions.stem ILIKE '%소장%'
      OR questions.stem ILIKE '%소송당사자%' OR questions.stem ILIKE '%집행권원%'
      OR questions.stem ILIKE '%경매%' OR questions.stem ILIKE '%배당%'
  )
  AND questions.sub_category IS NULL;

UPDATE questions SET sub_category = '가족법'
FROM subjects s
WHERE questions.subject_id = s.id AND s.name = '민사법'
  AND (  questions.stem ILIKE '%상속%' OR questions.stem ILIKE '%유류분%'
      OR questions.stem ILIKE '%혼인%' OR questions.stem ILIKE '%이혼%'
      OR questions.stem ILIKE '%친족%' OR questions.stem ILIKE '%가족%'
      OR questions.stem ILIKE '%친생%' OR questions.stem ILIKE '%입양%'
      OR questions.stem ILIKE '%후견%' OR questions.stem ILIKE '%피성년후견%'
      OR questions.stem ILIKE '%피한정후견%' OR questions.stem ILIKE '%부양%'
  )
  AND questions.sub_category IS NULL;

UPDATE questions SET sub_category = '상법'
FROM subjects s
WHERE questions.subject_id = s.id AND s.name = '민사법'
  AND (  questions.stem ILIKE '%주식%' OR questions.stem ILIKE '%주주총회%'
      OR questions.stem ILIKE '%이사회%' OR questions.stem ILIKE '%합명회사%'
      OR questions.stem ILIKE '%합자회사%' OR questions.stem ILIKE '%유한회사%'
      OR questions.stem ILIKE '%주식회사%' OR questions.stem ILIKE '%어음%'
      OR questions.stem ILIKE '%수표%' OR questions.stem ILIKE '%보험계약%'
      OR questions.stem ILIKE '%상행위%' OR questions.stem ILIKE '%상인%'
      OR questions.stem ILIKE '%상호%' OR questions.stem ILIKE '%사채%'
      OR questions.stem ILIKE '%이사의 책임%' OR questions.stem ILIKE '%주주의 권리%'
  )
  AND questions.sub_category IS NULL;

UPDATE questions SET sub_category = '물권법'
FROM subjects s
WHERE questions.subject_id = s.id AND s.name = '민사법'
  AND (  questions.stem ILIKE '%물권%' OR questions.stem ILIKE '%점유%'
      OR questions.stem ILIKE '%소유권%' OR questions.stem ILIKE '%지상권%'
      OR questions.stem ILIKE '%지역권%' OR questions.stem ILIKE '%전세권%'
      OR questions.stem ILIKE '%유치권%' OR questions.stem ILIKE '%질권%'
      OR questions.stem ILIKE '%저당권%' OR questions.stem ILIKE '%근저당%'
      OR questions.stem ILIKE '%선의취득%' OR questions.stem ILIKE '%명인방법%'
      OR questions.stem ILIKE '%부동산등기%' OR questions.stem ILIKE '%공시방법%'
  )
  AND questions.sub_category IS NULL;

UPDATE questions SET sub_category = '채권각론'
FROM subjects s
WHERE questions.subject_id = s.id AND s.name = '민사법'
  AND (  questions.stem ILIKE '%매매%' OR questions.stem ILIKE '%임대차%'
      OR questions.stem ILIKE '%위임%' OR questions.stem ILIKE '%불법행위%'
      OR questions.stem ILIKE '%부당이득%' OR questions.stem ILIKE '%도급%'
      OR questions.stem ILIKE '%고용%' OR questions.stem ILIKE '%증여%'
      OR questions.stem ILIKE '%교환%' OR questions.stem ILIKE '%임치%'
      OR questions.stem ILIKE '%소비대차%' OR questions.stem ILIKE '%사무관리%'
      OR questions.stem ILIKE '%하자담보%' OR questions.stem ILIKE '%계약해제%'
      OR questions.stem ILIKE '%계약의 해제%' OR questions.stem ILIKE '%해지%'
  )
  AND questions.sub_category IS NULL;

UPDATE questions SET sub_category = '채권총론'
FROM subjects s
WHERE questions.subject_id = s.id AND s.name = '민사법'
  AND (  questions.stem ILIKE '%채권%' OR questions.stem ILIKE '%채무%'
      OR questions.stem ILIKE '%이행불능%' OR questions.stem ILIKE '%이행지체%'
      OR questions.stem ILIKE '%손해배상%' OR questions.stem ILIKE '%동시이행%'
      OR questions.stem ILIKE '%위험부담%' OR questions.stem ILIKE '%연대채무%'
      OR questions.stem ILIKE '%보증%' OR questions.stem ILIKE '%채권양도%'
      OR questions.stem ILIKE '%상계%' OR questions.stem ILIKE '%경개%'
      OR questions.stem ILIKE '%채권자대위%' OR questions.stem ILIKE '%사해행위%'
      OR questions.stem ILIKE '%채무인수%'
  )
  AND questions.sub_category IS NULL;

-- 나머지 민사법 → 민법총론
UPDATE questions SET sub_category = '민법총론'
FROM subjects s
WHERE questions.subject_id = s.id AND s.name = '민사법'
  AND questions.sub_category IS NULL;

-- ════════════════════════════════════════════════════════════════
-- 공법 → 헌법 / 행정법 / 국제법
-- ════════════════════════════════════════════════════════════════

UPDATE questions SET sub_category = '국제법'
FROM subjects s
WHERE questions.subject_id = s.id AND s.name = '공법'
  AND (  questions.stem ILIKE '%국제법%' OR questions.stem ILIKE '%국제사법%'
      OR questions.stem ILIKE '%국제조약%' OR questions.stem ILIKE '%국제관습법%'
      OR questions.stem ILIKE '%국제기구%' OR questions.stem ILIKE '%외교%'
  )
  AND questions.sub_category IS NULL;

UPDATE questions SET sub_category = '행정법'
FROM subjects s
WHERE questions.subject_id = s.id AND s.name = '공법'
  AND (  questions.stem ILIKE '%행정법%' OR questions.stem ILIKE '%행정행위%'
      OR questions.stem ILIKE '%행정입법%' OR questions.stem ILIKE '%행정절차%'
      OR questions.stem ILIKE '%행정강제%' OR questions.stem ILIKE '%행정벌%'
      OR questions.stem ILIKE '%행정소송%' OR questions.stem ILIKE '%취소소송%'
      OR questions.stem ILIKE '%행정심판%' OR questions.stem ILIKE '%손실보상%'
      OR questions.stem ILIKE '%국가배상%' OR questions.stem ILIKE '%행정처분%'
      OR questions.stem ILIKE '%재량%' OR questions.stem ILIKE '%기속%'
      OR questions.stem ILIKE '%행정지도%' OR questions.stem ILIKE '%행정계획%'
      OR questions.stem ILIKE '%정보공개%' OR questions.stem ILIKE '%개인정보%'
      OR questions.stem ILIKE '%경찰행정%' OR questions.stem ILIKE '%공물%'
      OR questions.stem ILIKE '%영조물%'
  )
  AND questions.sub_category IS NULL;

-- 나머지 공법 → 헌법
UPDATE questions SET sub_category = '헌법'
FROM subjects s
WHERE questions.subject_id = s.id AND s.name = '공법'
  AND questions.sub_category IS NULL;

-- ════════════════════════════════════════════════════════════════
-- 법조윤리 → 그대로 유지 (sub_category 없음)
-- ════════════════════════════════════════════════════════════════
-- (No sub-categorization needed for 법조윤리)
