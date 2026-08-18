#!/usr/bin/env bash
# 로컬 채점 진행 상황. 실행 중 아무 때나 부르면 된다.
#   ./eval/progress.sh            한 번 보기
#   ./eval/progress.sh -w         2초마다 갱신 (Ctrl+C 로 종료)
# 기본값은 **리포 안**을 가리킨다. 예전에는 세션 scratchpad 절대경로가 박혀 있어
# 다른 세션·다른 기계에서는 그냥 동작하지 않았다.
#   PARTIAL=... ./eval/progress.sh   으로 언제든 덮어쓸 수 있다.
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# ★ 기본값은 **지금 돌고 있는 실행**을 가리켜야 쓸모가 있다. 리포트 명명이
#   `exp_NNN_{model}_{device}` 로 바뀐 뒤(계획서 §7.10) 이 값이 실제 파일명과
#   어긋나 있었다 — 기본값으로 돌리면 조용히 "0/160" 만 보여 준다.
P="${PARTIAL:-$REPO/eval/reports/exp_009_qwen35-4b-textonly_mps_partial.jsonl}"
# 언어 필터를 걸면 총 문항이 달라진다 (vi 41 · en 42 · ko 77 · 전체 160).
TOTAL="${TOTAL:-41}"

show() {
  [ -f "$P" ] || { echo "아직 시작 전 (파일 없음)"; return; }
  python3 - "$P" "$TOTAL" <<'PY'
import json,sys,os,time,collections
p,total=sys.argv[1],int(sys.argv[2])
rows=[json.loads(l) for l in open(p,encoding='utf-8') if l.strip()]
n=len(rows)
st=os.stat(p)
# macOS 는 append 마다 ctime 이 갱신된다 → 실제 생성 시각(st_birthtime)을 쓴다.
started=getattr(st,'st_birthtime',st.st_ctime); el=time.time()-started
rate=el/n if n else 0
eta=(total-n)*rate
bar='█'*int(28*n/total)+'·'*(28-int(28*n/total))
print(f"[{bar}] {n}/{total}  {100*n/total:.0f}%")
print(f"경과 {el/60:.0f}분 · 문항당 {rate:.0f}초 · 남은 시간 약 {eta/60:.0f}분")
gen=[r for r in rows if r.get('answer') and not r.get('fallback_reason')]
fb=collections.Counter(r['fallback_reason'] for r in rows if r.get('fallback_reason'))
print(f"정상 답변 {len(gen)}건 · 폴백 {sum(fb.values())}건  {dict(fb)}")
by=collections.Counter(r['lang'] for r in rows)
print("언어별 완료:", dict(by))
if rows: print("최근:", " ".join(r['qid'] for r in rows[-5:]))
PY
}

if [ "$1" = "-w" ]; then while true; do clear; show; sleep 2; done; else show; fi
