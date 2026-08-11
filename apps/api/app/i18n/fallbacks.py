"""폴백 응답 템플릿 (planner §7.5).

**LLM이 생성하지 않는다.** 폴백은 "모델을 신뢰할 수 없다"고 판단한 상황인데
거기서 모델을 다시 부르는 것은 모순이다. 사전 번역된 정적 문구를 쓴다.

이용자에게는 **사유를 그대로 노출하지 않는다.** `unsupported_number` 를
보여주면 "AI가 숫자를 지어냈다"는 사실만 전달되어 서비스 신뢰가 무너진다.
사유는 로그·지표에만 쓰고, 화면에는 "확인이 필요하다"는 취지의 문구를 낸다.
"""

from __future__ import annotations

from app.schemas.common import FallbackReason, Lang

# 공식 상담 채널. 폴백은 "답을 못 준다"로 끝나면 안 되고
# **다음 행동을 알려줘야** 한다.
CONTACTS: dict[Lang, list[str]] = {
    Lang.KO: [
        "금융감독원 금융상담 1332",
        "외국인종합안내센터 1345 (다국어 상담)",
    ],
    Lang.EN: [
        "Financial Supervisory Service counseling: 1332",
        "Foreigner Information Center: 1345 (multilingual)",
    ],
    Lang.VI: [
        "Tư vấn Cơ quan Giám sát Tài chính: 1332",
        "Trung tâm Thông tin Người nước ngoài: 1345 (đa ngôn ngữ)",
    ],
}

SCAM_CONTACTS: dict[Lang, list[str]] = {
    Lang.KO: ["경찰 신고 112", "금융감독원 1332", "거래 은행 고객센터 (지급정지 요청)"],
    Lang.EN: ["Police: 112", "Financial Supervisory Service: 1332",
              "Your bank's call center (request payment suspension)"],
    Lang.VI: ["Cảnh sát: 112", "Cơ quan Giám sát Tài chính: 1332",
              "Tổng đài ngân hàng của bạn (yêu cầu đình chỉ thanh toán)"],
}

# 사유별 문구. 여러 사유가 같은 문구를 공유한다 — 이용자 입장에서
# "근거를 찾지 못했다"와 "근거는 있는데 답변이 검사를 통과하지 못했다"는
# 구분할 실익이 없고, 후자를 밝히면 불안만 준다.
_TEMPLATES: dict[FallbackReason, dict[Lang, str]] = {
    FallbackReason.TIER_C: {
        Lang.KO: (
            "개별 승인 여부와 한도·금리는 각 금융기관의 심사 결과라서 안내해 드릴 수 없습니다. "
            "일반적인 요건을 확인하신 뒤 해당 기관에 직접 문의해 주세요."
        ),
        Lang.EN: (
            "Approval decisions, limits, and rates are determined by each financial "
            "institution's own review, so we cannot predict them. Please check the general "
            "requirements and contact the institution directly."
        ),
        Lang.VI: (
            "Việc phê duyệt, hạn mức và lãi suất do từng tổ chức tài chính tự thẩm định, "
            "nên chúng tôi không thể dự đoán. Vui lòng kiểm tra các yêu cầu chung và "
            "liên hệ trực tiếp với tổ chức đó."
        ),
    },
    FallbackReason.LOW_CONFIDENCE: {
        Lang.KO: (
            "이 질문에 정확히 답할 수 있는 공식 자료를 찾지 못했습니다. "
            "확인되지 않은 내용을 안내해 드릴 수는 없어, 공식 상담 창구를 안내해 드립니다."
        ),
        Lang.EN: (
            "We could not find official material that answers this question precisely. "
            "Rather than give you unverified information, here are the official channels."
        ),
        Lang.VI: (
            "Chúng tôi không tìm thấy tài liệu chính thức trả lời chính xác câu hỏi này. "
            "Thay vì đưa thông tin chưa được xác minh, đây là các kênh chính thức."
        ),
    },
    FallbackReason.SCAM_VERDICT: {
        Lang.KO: (
            "특정 연락이나 거래가 사기인지 여부는 판단해 드릴 수 없습니다. "
            "의심되는 상황이라면 즉시 아래로 연락하시고, 송금이나 개인정보 제공을 중단하세요."
        ),
        Lang.EN: (
            "We cannot judge whether a specific contact or transaction is a scam. "
            "If you suspect fraud, contact the numbers below immediately and stop any "
            "transfers or sharing of personal information."
        ),
        Lang.VI: (
            "Chúng tôi không thể phán đoán một liên hệ hay giao dịch cụ thể có phải lừa đảo hay không. "
            "Nếu nghi ngờ, hãy liên hệ ngay các số dưới đây và ngừng chuyển tiền hoặc "
            "cung cấp thông tin cá nhân."
        ),
    },
    FallbackReason.MODEL_REFUSAL: {
        Lang.KO: (
            "이 질문에는 답변을 제공하지 않습니다. 금융 절차에 관한 일반적인 문의라면 "
            "다시 질문해 주시고, 급한 사안이라면 아래 공식 창구를 이용해 주세요."
        ),
        Lang.EN: (
            "We are not able to answer this question. If you have a general question about "
            "financial procedures, please rephrase it, or use the official channels below."
        ),
        Lang.VI: (
            "Chúng tôi không thể trả lời câu hỏi này. Nếu bạn có câu hỏi chung về thủ tục "
            "tài chính, vui lòng hỏi lại, hoặc sử dụng các kênh chính thức dưới đây."
        ),
    },
    FallbackReason.UPSTREAM_ERROR: {
        Lang.KO: (
            "일시적인 오류로 답변을 생성하지 못했습니다. 잠시 후 다시 시도해 주세요. "
            "급하시면 아래 공식 창구를 이용해 주세요."
        ),
        Lang.EN: (
            "A temporary error prevented us from generating an answer. Please try again "
            "shortly, or use the official channels below if it is urgent."
        ),
        Lang.VI: (
            "Lỗi tạm thời khiến chúng tôi không tạo được câu trả lời. Vui lòng thử lại sau, "
            "hoặc dùng các kênh chính thức dưới đây nếu gấp."
        ),
    },
    FallbackReason.INJECTION_BLOCKED: {
        Lang.KO: "질문을 이해하지 못했습니다. 금융 절차에 대해 궁금한 점을 다시 말씀해 주세요.",
        Lang.EN: "We could not understand the question. Please rephrase what you would like "
                 "to know about financial procedures.",
        Lang.VI: "Chúng tôi không hiểu câu hỏi. Vui lòng diễn đạt lại điều bạn muốn biết "
                 "về thủ tục tài chính.",
    },
}

# 근거는 있었으나 출력 검사를 통과하지 못한 경우 — 이용자에게는
# low_confidence 와 동일하게 노출한다(planner §7.5).
_ALIASES: dict[FallbackReason, FallbackReason] = {
    FallbackReason.NO_CITATION: FallbackReason.LOW_CONFIDENCE,
    FallbackReason.PHANTOM_CITATION: FallbackReason.LOW_CONFIDENCE,
    FallbackReason.UNSUPPORTED_NUMBER: FallbackReason.LOW_CONFIDENCE,
    FallbackReason.FORBIDDEN_EXPRESSION: FallbackReason.LOW_CONFIDENCE,
    FallbackReason.CREDENTIAL_REQUEST: FallbackReason.LOW_CONFIDENCE,
    FallbackReason.OUT_OF_SCOPE: FallbackReason.LOW_CONFIDENCE,
}

# 계층 B 강제 문구 (planner §7.1). 기관 개별 명시 근거가 없을 때 붙인다.
_GENERIC_NOTICE: dict[Lang, str] = {
    Lang.KO: "※ 일반적인 안내입니다. 기관마다 요건이 다를 수 있으므로 방문 전에 "
             "해당 금융기관에 반드시 확인하세요.",
    Lang.EN: "* This is general guidance. Requirements vary by institution — please "
             "confirm with the financial institution before visiting.",
    Lang.VI: "* Đây là hướng dẫn chung. Yêu cầu có thể khác nhau tùy tổ chức — vui lòng "
             "xác nhận với tổ chức tài chính trước khi đến.",
}


def fallback_text(reason: FallbackReason, lang: Lang) -> str:
    """사유에 대응하는 정적 문구. 언어가 없으면 한국어로 폴백한다."""
    effective = _ALIASES.get(reason, reason)
    table = _TEMPLATES.get(effective) or _TEMPLATES[FallbackReason.LOW_CONFIDENCE]
    return table.get(lang) or table[Lang.KO]


def fallback_contacts(reason: FallbackReason, lang: Lang) -> list[str]:
    table = SCAM_CONTACTS if reason is FallbackReason.SCAM_VERDICT else CONTACTS
    return table.get(lang) or table[Lang.KO]


def generic_notice(lang: Lang) -> str:
    return _GENERIC_NOTICE.get(lang) or _GENERIC_NOTICE[Lang.KO]


def prepend_generic_notice(answer: str, lang: Lang) -> str:
    """계층 B 강제 문구를 삽입한다. 이미 있으면 중복하지 않는다."""
    notice = generic_notice(lang)
    if notice in answer:
        return answer
    return f"{answer.rstrip()}\n\n{notice}"
