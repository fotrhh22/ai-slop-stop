import type { Attempt } from "../types";

interface Props {
  attempt: Attempt;
  busy: boolean; // 다른 생성/평가 진행 중이면 중복 클릭 방지
  staffMode: boolean; // STAFF 모드일 때만 수동 PASS 전환 버튼 노출
  onTry: (id: string) => void;
  onStaffPass: (id: string) => void;
  onReceiptRetry: (id: string) => void;
}

// 시도 카드: 프롬프트 -> Try -> 생성 이미지 -> 항목별 비교/판정
export function AttemptCard({
  attempt: a,
  busy,
  staffMode,
  onTry,
  onStaffPass,
  onReceiptRetry,
}: Props) {
  const generating = a.genStatus === "generating";
  const evaluating = a.evalStatus === "evaluating";
  const canAct = a.genStatus === "ready" && !busy;
  const evaluated = a.evalStatus === "done";
  const passed = evaluated && a.verdict === "PASS";

  return (
    <div className="attempt-card">
      <div className="attempt-card__prompt">{a.prompt}</div>

      {/* PASS 가 되면 Try/STAFF 버튼을 모두 숨긴다 */}
      {!passed && (
        <div className="attempt-card__actions">
          <button
            className="btn-try"
            disabled={!canAct || evaluating}
            onClick={() => onTry(a.id)}
          >
            {evaluating ? "평가 중…" : "Try · 평가하기"}
          </button>
          {/* STAFF 모드: 생성 완료 후 평가 여부와 관계없이 수동 전환 */}
          {staffMode && a.genStatus === "ready" && (
            <button className="btn-staff" disabled={busy} onClick={() => onStaffPass(a.id)}>
              STAFF · 수동 PASS
            </button>
          )}
        </div>
      )}

      {a.receiptPending && <p role="status">성공 기록을 저장·확인하는 중…</p>}
      {a.receiptError && <div className="receipt-save-error" role="alert">
        <p>영수증 저장/확인 실패: {a.receiptError}</p>
        {a.verdict === "PASS" && <button disabled={a.receiptPending} onClick={() => onReceiptRetry(a.id)}>저장 재시도 · 모델 재호출 없음</button>}
      </div>}

      <div className="attempt-card__image">
        {generating ? (
          <div className="attempt-card__placeholder">이미지 생성 중…</div>
        ) : a.genStatus === "error" ? (
          <div className="attempt-card__placeholder is-error">
            생성 오류: {a.genError ?? "알 수 없음"} · 같은 프롬프트를 다시 입력해 재생성하세요.
          </div>
        ) : a.imageUrl ? (
          <img src={a.imageUrl} alt="생성 이미지" />
        ) : null}
      </div>

      <div className="attempt-card__result">
        {evaluated && a.criteria && (
          <div className="attempt-card__criteria" aria-label="Gemini 항목별 비교">
            {([['subject', '주요 피사체'], ['composition', '구도'], ['color', '색감'], ['background', '배경']] as const).map(([key, label]) => (
              <span key={key}>{label}: <strong>{a.criteria![key] === 'MATCH' ? '유사' : '차이 있음'}</strong></span>
            ))}
          </div>
        )}
        <div className="attempt-card__verdict">
          <span className="k">판정</span>
          {a.evalStatus === "evaluating" ? (
            <span className="v muted">평가 중…</span>
          ) : a.evalStatus === "error" ? (
            <span className="v muted">평가 오류 — 다시 Try</span>
          ) : evaluated && a.verdict ? (
            <>
              <span className={"badge " + (a.verdict === "PASS" ? "pass" : "fail")}>
                {a.verdict}
              </span>
              {a.staffOverride && <span className="staff-tag">STAFF 수동</span>}
              {!a.staffOverride && a.routerModel && <span className="by">by {a.routerModel}</span>}
            </>
          ) : (
            <span className="v muted">평가 전</span>
          )}
        </div>
        {evaluated && a.reason && (
          <div className="attempt-card__reason">이유: {a.reason}</div>
        )}
        {a.evalStatus === "error" && a.evalError && (
          <div className="attempt-card__reason is-error">오류: {a.evalError}</div>
        )}
      </div>
    </div>
  );
}
