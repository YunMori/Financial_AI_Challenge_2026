"""요건 매트릭스 (planner §4.2) — F2 계좌개설 내비게이터의 데이터 층.

판정은 전부 규칙이다. 이 패키지에 LLM 호출은 없다.
"""

from app.matrix.loader import Evidence, Institution, Matrix, VisaRule, get_matrix

__all__ = ["Evidence", "Institution", "Matrix", "VisaRule", "get_matrix"]
