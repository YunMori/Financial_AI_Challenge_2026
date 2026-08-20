"""체크리스트 고정 문구 (F3).

**서버가 만드는 PDF 는 프론트의 `messages/*.json` 을 볼 수 없다.** 그래서
문서용 문자열은 여기 둔다. 프론트와 중복되는 항목이 있지만, 하나로 합치려면
빌드 타임에 JSON 을 공유해야 하고 그 배선이 문서 몇 줄보다 비싸다.

★ **본문(서류명·주의문구)은 여기서 번역하지 않는다.** 서류명은
`corpus/glossary/glossary.csv`, 주의문구는 `corpus/matrix/documents.yaml` 이
소스다. 여기 있는 것은 **표의 머리말과 안내문**뿐이다.
"""

from __future__ import annotations

from app.schemas.common import Lang

TITLE: dict[Lang, str] = {
    Lang.KO: "K-Buddy 서류 체크리스트",
    Lang.EN: "K-Buddy Document Checklist",
    Lang.VI: "Danh sách giấy tờ K-Buddy",
}

SECTION_TITLES: dict[str, dict[Lang, str]] = {
    "base": {
        Lang.KO: "① 공통 신분 서류",
        Lang.EN: "1. Identification (all cases)",
        Lang.VI: "1. Giấy tờ tùy thân (mọi trường hợp)",
    },
    "account_opening": {
        Lang.KO: "② 계좌개설·한도해제 증빙 (금융위원회)",
        Lang.EN: "2. Proof of transaction purpose — banks (FSC)",
        Lang.VI: "2. Chứng minh mục đích giao dịch — ngân hàng (FSC)",
    },
    "foreign_registration": {
        Lang.KO: "③ 외국인등록 제출서류 (법무부) — 은행 서류가 아닙니다",
        Lang.EN: "3. Foreign registration documents (MOJ) — not for banks",
        Lang.VI: "3. Giấy tờ đăng ký người nước ngoài (MOJ) — không dùng cho ngân hàng",
    },
}

# 표 머리말. 모국어 / 한국어 2열 병기 — 창구에서 그대로 보여주는 용도다.
COL_LOCAL: dict[Lang, str] = {
    Lang.KO: "서류",
    Lang.EN: "Document",
    Lang.VI: "Giấy tờ",
}
COL_KO: dict[Lang, str] = {
    Lang.KO: "한국어 표기",
    Lang.EN: "Korean (show this at the counter)",
    Lang.VI: "Tiếng Hàn (đưa cho nhân viên xem)",
}

PROFILE_LABELS: dict[str, dict[Lang, str]] = {
    "visa": {Lang.KO: "체류자격", Lang.EN: "Visa", Lang.VI: "Tư cách lưu trú"},
    "purpose": {Lang.KO: "거래목적", Lang.EN: "Purpose", Lang.VI: "Mục đích"},
    "institution": {Lang.KO: "기관", Lang.EN: "Institution", Lang.VI: "Tổ chức"},
    "generated_at": {Lang.KO: "발급 기준일", Lang.EN: "Generated on", Lang.VI: "Ngày tạo"},
}

SOURCES: dict[Lang, str] = {
    Lang.KO: "출처",
    Lang.EN: "Sources",
    Lang.VI: "Nguồn",
}

NOT_CONFIRMED: dict[Lang, str] = {
    Lang.KO: "이 항목은 공식 문서로 확인하지 못했습니다. 해당 은행에 직접 확인하세요.",
    Lang.EN: "This item could not be confirmed in official sources. Please ask the bank directly.",
    Lang.VI: "Mục này chưa được xác nhận trong tài liệu chính thức. Vui lòng hỏi trực tiếp ngân hàng.",
}

# planner §11.2 — 화면에 상시 존재해야 하는 고지가 인쇄물에도 따라간다.
FINAL_AUTHORITY: dict[Lang, str] = {
    Lang.KO: "최종 확인 주체는 금융기관입니다. 이 문서는 안내이며 심사 결과가 아닙니다.",
    Lang.EN: "The financial institution makes the final decision. This document is "
             "guidance, not an approval.",
    Lang.VI: "Tổ chức tài chính đưa ra quyết định cuối cùng. Tài liệu này chỉ mang tính "
             "hướng dẫn, không phải kết quả xét duyệt.",
}

ANTI_PHISHING: dict[Lang, str] = {
    Lang.KO: "K-Buddy 는 계좌번호·비밀번호를 요구하지 않습니다.",
    Lang.EN: "K-Buddy never asks for your account number or password.",
    Lang.VI: "K-Buddy không bao giờ hỏi số tài khoản hoặc mật khẩu của bạn.",
}


def pick(table: dict[Lang, str], lang: Lang) -> str:
    """언어가 없으면 한국어로 폴백한다 — 빈 칸보다 낫다."""
    return table.get(lang) or table[Lang.KO]


def section_title(key: str, lang: Lang) -> str:
    return pick(SECTION_TITLES[key], lang)
