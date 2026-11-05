"""Fixed labels assigned before router inference. No new image generation."""
GOALS = {
    'apple': {'required':['subject','color','background'], 'checks':'빨간 사과 한 개, 흰 배경. 사과 품종·작은 반점·그림자·프레임 여백 차이는 허용한다.'},
    'crocodile-bomber': {'required':['subject','composition','color','background'], 'checks':'기수 부분을 대체한 악어 머리, 은색 비행기 동체, 기체 아래 폭탄, 아래쪽 시점과 앞부분 위주 구도, 비사실적 애니메이션 스타일, 맑은 하늘과 구름. 곰·고양이·하마는 악어로 인정하지 않는다.'},
    'spatial': {'required':['subject','composition','color'], 'checks':'왼쪽 파란 정육면체, 오른쪽 빨간 구, 두 물체 뒤쪽 노란 원뿔, 총 세 물체. 회색/흰색 중립 배경과 그림자·크기·시점의 작은 차이는 허용한다. 정육면체 대신 구나 다면체는 허용하지 않는다.'},
    'count-style': {'required':['subject','composition','color','background'], 'checks':'노란 오리 세 마리, 오른쪽으로 향함, 가운데 오리만 빨간 모자, 연두색 배경, 검은 윤곽선의 평면 일러스트. 노란 배경이나 짙은 청록 배경은 연두색이 아니다.'},
}

PAIRS = [
    ('apple-4b-pass','apple','klein-4b-bf16','apple','PASS','사과·색상·배경 일치'),
    ('croc-4b-fail','crocodile-bomber','klein-4b-bf16','crocodile-bomber','FAIL','악어 대신 다른 동물, 사실적인 스타일'),
    ('spatial-4b-pass','spatial','klein-4b-bf16','spatial','PASS','도형·색상·배치 일치, 작은 시점 차이 허용'),
    ('ducks-z-fail','count-style','z-image-turbo','count-style','FAIL','연두색 대신 노란 배경'),
    ('apple-z-pass','apple','z-image-turbo','apple','PASS','사과·색상·배경 일치'),
    ('croc-z-fail','crocodile-bomber','z-image-turbo','crocodile-bomber','FAIL','악어 대신 곰, 폭탄 불명확'),
    ('spatial-qwen-fail','spatial','qwen-image-lightning-nf4','spatial','FAIL','정육면체 대신 다면체'),
    ('spatial-z-pass','spatial','z-image-turbo','spatial','PASS','도형·색상·배치 일치'),
    ('apple-duck-fail','apple','klein-9b-nf4','count-style','FAIL','완전히 다른 피사체'),
    ('croc-apple-fail','crocodile-bomber','klein-4b-bf16','apple','FAIL','완전히 다른 피사체'),
]

IMPROVED = '''
추가 판정 규칙:
전체 분위기가 닮았다는 이유로 필수 조건을 생략하지 마라. 아래 목표별 필수 조건을 TARGET와 GENERATED에서 직접 확인하라.
동물의 정확한 종류, 도형 종류, 개수, 좌우/앞뒤 관계, 필수 물체의 존재와 지정 배경색을 구별하라.
없는 것을 있다고 추측하지 말고, 필수 조건을 확인할 수 없으면 해당 criteria를 MISMATCH로 표시하라.
subject에는 주요 피사체/종류/개수/필수 물체를, composition에는 배치/방향/주요 스타일을 반영하라.
reason에는 가장 중요한 실제 일치 또는 차이를 짧게 적어라.
모든 필수 category가 MATCH이고 다른 핵심 오류가 없을 때만 PASS하라. 허용한 작은 차이로 FAIL하지 마라.
목표별 필수 조건: {checks}
필수 category: {required}
기존과 동일한 verdict/reason/criteria JSON 형식만 반환하라.
'''


def aggregate(raw, required):
    if raw['verdict']=='PASS' and any(raw['criteria'][key]!='MATCH' for key in required):
        return 'FAIL'
    return raw['verdict']
