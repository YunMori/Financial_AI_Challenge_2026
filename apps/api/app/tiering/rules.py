"""계층 C 트리거 규칙 (planner §7.2).

계층 C는 **개별 심사 결과**다 — 특정 이용자의 승인 여부, 한도, 금리.
이건 금융기관이 심사해야 알 수 있고, AI가 예측하면 그 자체가 오안내가 된다.

**LLM에 도달하기 전에 규칙으로 차단한다.** 가장 안전하고 빠르며, 무엇보다
"프롬프트가 아니라 아키텍처로 강제한다"는 주장의 실체가 된다. 프롬프트로만
막으면 인젝션 한 줄에 뚫린다.
"""

from __future__ import annotations

import re
from enum import StrEnum


class BlockReason(StrEnum):
    INDIVIDUAL_APPROVAL = "individual_approval"  # 내가 승인될까요?
    LIMIT_PREDICTION = "limit_prediction"        # 얼마까지 받을 수 있나요?
    RATE_PREDICTION = "rate_prediction"          # 금리 몇 %로 나오나요?
    SCREENING_RESULT = "screening_result"        # 심사 결과 알려주세요
    RANKING = "ranking"                          # 어느 은행이 제일 좋아요?
    SCAM_VERDICT = "scam_verdict"                # 이거 사기인가요?


# 개별 승인·한도·금리 예측. 한국어 어순이 자유로우므로 `.{0,N}` 로 느슨하게 잇는다.
TIER_C_PATTERNS: tuple[tuple[re.Pattern[str], BlockReason], ...] = (
    # "제가 ~ 될까요 / 가능할까요 / 승인되나요"
    (re.compile(r"(내가|제가|나는|저는|우리|본인).{0,25}?(될까|되나요|되나|가능할까|가능한가|승인|통과|받을 수|있을까|있나요|있는지)"),
     BlockReason.INDIVIDUAL_APPROVAL),
    # "얼마까지 빌릴 수 있나 / 한도가 얼마나 나오나"
    (re.compile(r"(얼마나|얼마까지|얼마정도|얼마 정도).{0,20}?(빌릴|대출|한도|받을|나올|가능)"),
     BlockReason.LIMIT_PREDICTION),
    (re.compile(r"(한도|대출).{0,15}?(얼마|몇).{0,10}?(나올|나오|가능|받을)"),
     BlockReason.LIMIT_PREDICTION),
    # "금리 몇 퍼센트 적용되나요"
    (re.compile(r"(금리|이율|이자율).{0,15}?(몇|얼마).{0,10}?(나올|나오|적용|되)"),
     BlockReason.RATE_PREDICTION),
    # "심사 결과 예측해줘"
    (re.compile(r"(심사|승인|허가).{0,10}?(결과|여부).{0,10}?(알려|예측|말해|어떻게)"),
     BlockReason.SCREENING_RESULT),
    # 순위·최고 추천 → 중립성 위반. 계층 C가 아니라 조건별 비교로 전환한다.
    (re.compile(r"(어느|어떤|무슨).{0,10}?(은행|금융기관|상품).{0,10}?(제일|가장|최고|나은|좋)"),
     BlockReason.RANKING),
    (re.compile(r"(추천|골라|뽑아).{0,10}?(줘|주세요|해줘|해주세요)"),
     BlockReason.RANKING),
    # 사기 여부 판정 — F8 이 명시적으로 금지하는 것
    (re.compile(r"(이|그|저|이번|방금).{0,10}?(전화|문자|메시지|링크|사람).{0,15}?(사기|피싱|보이스피싱)"),
     BlockReason.SCAM_VERDICT),
    (re.compile(r"(사기|피싱).{0,10}?(인가요|맞나요|맞아요|인지|일까요)"),
     BlockReason.SCAM_VERDICT),
)

# 다국어 질의는 ③ 정규화 후의 한국어 검색어에 대해 한 번 더 검사한다.
# 아래는 정규화 전에도 잡히도록 하는 최소한의 영어 패턴이다.
TIER_C_PATTERNS_EN: tuple[tuple[re.Pattern[str], BlockReason], ...] = (
    (re.compile(r"\b(will|can|would)\s+(i|my|we)\b.{0,30}?\b(approv|qualify|eligible|get|accepted)",
                re.I), BlockReason.INDIVIDUAL_APPROVAL),
    (re.compile(r"\bhow much\b.{0,30}?\b(can|could|will)\s+(i|we)\b.{0,20}?\b(borrow|get|loan)",
                re.I), BlockReason.LIMIT_PREDICTION),
    (re.compile(r"\b(which|what)\s+bank\b.{0,20}?\b(best|better|recommend)", re.I),
     BlockReason.RANKING),
    (re.compile(r"\bis\s+(this|that|it)\b.{0,20}?\b(scam|phishing|fraud)", re.I),
     BlockReason.SCAM_VERDICT),
)


def match_tier_c(text: str) -> BlockReason | None:
    """계층 C 트리거를 찾는다. 없으면 None.

    한국어·영어 패턴을 모두 본다. 그 외 언어는 정규화 후 한국어 검색어에
    대해 다시 호출한다(2차 방어).
    """
    for pattern, reason in (*TIER_C_PATTERNS, *TIER_C_PATTERNS_EN):
        if pattern.search(text):
            return reason
    return None


# 금지 표현 — 출력 검사용 (planner §8.3).
# 이런 단어가 나온 답변은 근거가 무엇이든 내보내지 않는다. 개별 심사 결과를
# 단정하는 순간 서비스가 보조수단성을 벗어나기 때문이다.
FORBIDDEN_EXPRESSIONS: tuple[re.Pattern[str], ...] = (
    re.compile(r"반드시\s*(승인|통과|가능)"),
    re.compile(r"(승인|통과)(을|를)?\s*보장"),
    re.compile(r"100%\s*(승인|가능|통과)"),
    re.compile(r"확실히\s*(됩니다|가능합니다|승인)"),
    re.compile(r"틀림없이\s*(됩니다|가능)"),
    re.compile(r"\bguarantee(d|s)?\s+(approval|acceptance)", re.I),
    re.compile(r"\byou\s+will\s+(definitely|certainly)\s+(be\s+)?approv", re.I),
)

# 이용자에게 민감정보를 요구하는 문장 — 서비스 사칭 대응 (planner §11장).
# 우리 서비스는 계좌번호·비밀번호를 절대 묻지 않으므로, 답변에 이런 요구가
# 나오면 그 자체가 사고다.
CREDENTIAL_REQUEST: tuple[re.Pattern[str], ...] = (
    re.compile(r"(계좌번호|비밀번호|보안카드|OTP|공인인증서|카드번호).{0,15}?(알려|입력|보내|말씀|제출)"),
    re.compile(r"(주민등록번호|외국인등록번호).{0,15}?(알려|입력|보내|말씀)"),
    re.compile(r"\b(tell|send|enter|provide)\s+(me\s+)?(your\s+)?"
               r"(account\s+number|password|pin|otp)", re.I),
)


def find_forbidden(text: str) -> str | None:
    for pattern in FORBIDDEN_EXPRESSIONS:
        if m := pattern.search(text):
            return m.group(0)
    return None


def find_credential_request(text: str) -> str | None:
    for pattern in CREDENTIAL_REQUEST:
        if m := pattern.search(text):
            return m.group(0)
    return None
