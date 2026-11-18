"""Versioned questionnaires and learning content. Answer keys stay server-side."""
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "content"
TRAINING = json.loads((CONTENT / "server/training-a-low.private.json").read_text())
TRAINING_PUBLIC = json.loads((CONTENT / "public/training-a-low.json").read_text())
WRITING = [
    "이미지를 보고 프롬프트에 포함할 핵심 특징을 골라낼 수 있다.",
    "대상의 색상·개수·위치·관계를 구체적인 문장으로 설명할 수 있다.",
    "생성 결과가 목표와 다를 때 어떤 부분을 수정해야 하는지 판단할 수 있다.",
    "AI에 요청하기 전에 필요한 조건을 정리한다.",
]
FEATURES = [
    {"id": "optimizer", "name": "프롬프트 개선기", "description": "작성한 질문을 더 구체적으로 다듬은 예시와 수정 이유를 제공합니다."},
    {"id": "recommendation", "name": "AI 모델 추천", "description": "원하는 작업에 맞는 AI 모델을 추천하고, 특징과 추천 이유를 비교해 줍니다."},
    {"id": "tip", "name": "하루의 팁", "description": "프롬프트 작성법과 AI 활용 방법을 매일 짧은 팁으로 제공합니다."},
    {"id": "game", "name": "미니게임·이미지 챌린지", "description": "앞서 제시된 키워드 학습 방식의 챌린지를 진행합니다. 템플런·다이노·길건너 친구들 형태의 미니게임 성적에 비례해 챌린지 시도 횟수를 얻고, 챌린지에 성공하면 보상 포인트를 받습니다."},
    {"id": "garden", "name": "정원 꾸미기·소셜", "description": "활동으로 얻은 보상 포인트(EP)로 캐릭터와 정원을 꾸밉니다. 친구의 정원을 방문하며 활동을 이어갈 수 있습니다."},
]
PRE_OPTIONS = {
    "ai_frequency": ["사용한 적 없다", "월 1회 미만", "월 1~3회", "주 1~3회", "주 4회 이상"],
    "image_experience": ["없다", "1~2회 있다", "여러 번 사용해 봤다", "정기적으로 사용한다"],
    "education": ["배워본 적 없다", "영상·자료 등을 보며 스스로 배웠다", "학교 수업·특강·교육 강좌에서 배웠다", "수업·강좌와 자기 학습을 모두 경험했다"],
    "error_strategy": ["같은 프롬프트로 다시 요청한다", "부족한 부분의 조건을 추가하거나 수정한다", "프롬프트를 처음부터 새로 작성한다", "다른 AI 서비스·모델을 사용한다", "더 이상 시도하지 않는다", "경험이 없어 모르겠다"],
    "difficulties": ["어떤 작업에 AI를 활용할지 정하기", "원하는 결과를 구체적인 지시문으로 작성하기", "결과의 문제점을 파악하고 프롬프트 수정하기", "작업 목적에 맞는 AI 서비스·모델 고르기", "AI 서비스의 기능과 조작 방법 익히기", "특별히 어렵지 않다", "사용 경험이 없어 모르겠다"],
}
PRE_LABELS = {"ai_frequency": "생성형 AI를 얼마나 자주 사용하나요?", "image_experience": "AI 이미지 생성 경험이 있나요?", "education": "AI 활용 방법이나 프롬프트 작성법을 배워본 적이 있나요?", "error_strategy": "AI 산출물(글·이미지·동영상 등)이 원하는 결과와 다를 때 주로 어떻게 하나요?", "difficulties": "AI를 활용할 때 어려운 점을 최대 2개 골라 주세요."}
TRAINING_ITEMS = ["키워드 퀴즈가 관찰해야 할 특징을 찾는 데 도움이 됐다.", "선택 과정에서 처음 프롬프트에 빠진 조건을 발견했다.", "해설이 키워드를 문장으로 연결하는 데 도움이 됐다.", "퀴즈와 해설을 활용한 학습이 흥미로웠다."]
REVISIT = ["매일 새로운 이미지의 키워드 문제를 풀고 프롬프트 작성 해설을 보기 위해 다시 방문하고 싶다.", "매일 제공되는 AI 활용 팁을 확인하기 위해 다시 방문하고 싶다.", "프롬프트 개선기로 내가 작성한 질문을 다듬고 수정 이유를 확인하기 위해 다시 방문하고 싶다.", "새로운 작업을 할 때 적합한 AI 모델과 추천 이유를 확인하기 위해 다시 방문하고 싶다.", "미니게임으로 키워드 챌린지의 시도 기회를 얻기 위해 다시 방문하고 싶다.", "보상 포인트로 정원·캐릭터를 꾸미고 친구의 정원을 방문하기 위해 다시 방문하고 싶다."]


PROMPT_LIMITS = {"pre": 50, "post": 100}


def config(mode):
    value = json.loads((CONTENT / "server/study.json").read_text())
    value["id"] = os.getenv("POC_STUDY_ID", value["id"] + "-" + mode)
    value["mode"] = mode
    value["target_approved"] = value["target_approved"] or os.getenv("POC_TARGET_APPROVED", "false").lower() == "true"
    value["router_output_format"] = "pass-fail-v1"
    value["profiles"] = {"gemini": {"image_model": os.getenv("POC_GEMINI_IMAGE_MODEL", os.getenv("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-lite-image")), "router_model": os.getenv("POC_GEMINI_ROUTER_MODEL", os.getenv("GEMINI_ROUTER_MODEL", "gemini-3.5-flash-lite"))}, "runpod": {"image_model": value["image_model"], "router_model": value["router_model"]}}
    value["flow_version"] = "result-next-v5-two-post-attempts"
    value["post_attempt_limit"] = 2
    value["prompt_limits"] = PROMPT_LIMITS
    value["is_test"] = mode == "mock" or os.getenv("POC_TEST_MODE", "false").lower() == "true"
    value["target_sha256"] = hashlib.sha256((CONTENT / "public/target-a.png").read_bytes()).hexdigest()
    value["training_version"] = TRAINING["version"]
    value["training_sha256"] = TRAINING["assets"]["sha256"]
    actual_training = hashlib.sha256((CONTENT / "public/training-a-low.png").read_bytes()).hexdigest()
    if actual_training != value["training_sha256"]:
        raise RuntimeError("Training image does not match the frozen A-low manifest")
    if value["target_sha256"] == actual_training:
        raise RuntimeError("Assessment A must differ from learning example B")
    value["router_prompt_sha256"] = hashlib.sha256((CONTENT / "server/evaluate.md").read_bytes()).hexdigest()
    value["survey_version"] = "poc-survey-v5"
    value["questionnaire_sha256"] = hashlib.sha256(json.dumps([WRITING, FEATURES, PRE_OPTIONS, PRE_LABELS, TRAINING_ITEMS, REVISIT], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return value


def explanation():
    return {"prompt": TRAINING["answer_prompt"], "answers": [TRAINING["display_labels"][k["id"]] for k in TRAINING["correct_keywords"]],
            "tips": TRAINING["tips"], "image_source": TRAINING["source_label"], "image": "/content/training-a-low.png"}
