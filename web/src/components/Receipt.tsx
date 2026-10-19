import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import type { Receipt as ReceiptData } from "../types";

export function Receipt({ receipt: r, onClose }: { receipt: ReceiptData; onClose: () => void }) {
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    close.current?.focus();
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", key);
    return () => { window.removeEventListener("keydown", key); previous?.focus(); };
  }, [onClose]);
  return createPortal(<div className="receipt-overlay" role="dialog" aria-modal="true" aria-label="성공 영수증">
    <article className="receipt-paper">
      <div className="receipt-actions">
        <button onClick={() => window.print()}>인쇄 / PDF 저장</button>
        <button ref={close} onClick={onClose}>닫기</button>
      </div>
      <p className="receipt-eyebrow">AI SLOP STOP CHALLENGE</p>
      <h1>PASS 영수증</h1>
      <p className={r.verified ? "receipt-ok" : "receipt-warning"}>
        {r.source === "mock" ? "샘플 · DB에 저장되지 않은 미리보기" : r.verified ? "Supabase 저장 및 원장 대조 완료" : "DB 저장됨 · 원장 대조 불일치 확인 필요"}
      </p>
      <dl>
        <dt>세션 ID</dt><dd>{r.challenge_id}</dd>
        <dt>영수증 ID</dt><dd>{r.id}</dd>
        <dt>저장 시각</dt><dd>{new Date(r.created_at).toLocaleString("ko-KR")}</dd>
        <dt>PASS 출처</dt><dd>{r.pass_source === "staff" ? `STAFF · ${r.staff_actor ?? "샘플"}` : "LLM Router"}</dd>
        {r.pass_source === "staff" && <><dt>원래 Router 판정</dt><dd>{r.router_verdict ?? "미평가"}</dd><dt>승인 사유</dt><dd>{r.staff_reason ?? "샘플"}</dd></>}
      </dl>
      <img className="receipt-image" src={r.image_url} alt="PASS 받은 생성 이미지" />
      <h2>성공 프롬프트 원문</h2><pre>{r.prompt}</pre>
      <h2>성공까지 누적 · FAIL 포함</h2>
      <dl>
        <dt>이미지 생성 호출</dt><dd>{r.generation_calls}회</dd>
        <dt>Router 호출</dt><dd>{r.router_calls}회</dd>
        <dt>전체 모델 호출</dt><dd>{r.model_calls_total}회</dd>
        <dt>API 오류 호출</dt><dd>{r.failed_calls}회</dd>
        <dt>확인된 총 토큰</dt><dd>{r.known_tokens_total.toLocaleString()}</dd>
        <dt>토큰 미확인 호출</dt><dd>{r.calls_without_total_tokens}회</dd>
      </dl>
      {!r.usage_complete && <p className="receipt-warning">사용량 미확인 호출이 있어 실제 전체 토큰은 위 합계보다 많을 수 있습니다.</p>}
      <h2>모델별 사용량</h2>
      {Object.entries(r.tokens_by_model).map(([model, u]) => <section className="receipt-model" key={model}>
        <strong>{model}</strong>
        <dl><dt>호출</dt><dd>{u.calls}회</dd><dt>입력 토큰 합계</dt><dd>{u.known_input_tokens?.toLocaleString()}</dd>
          <dt>출력 토큰 합계</dt><dd>{u.known_output_tokens?.toLocaleString()}</dd>
          <dt>추론 토큰 합계</dt><dd>{u.known_thought_tokens?.toLocaleString()}</dd>
          <dt>공급자 총 토큰 합계</dt><dd>{u.known_total_tokens?.toLocaleString()}</dd></dl>
      </section>)}
      <p className="receipt-note">이미지 생성과 Router의 실제 API 호출을 집계합니다. 추론 토큰을 총 토큰에 다시 더하지 않습니다.</p>
      {r.source === "supabase" && <details><summary>DB 검증 및 호출 원장</summary>
        {Object.entries(r.verification).map(([name, ok]) => <p key={name}>{ok ? "✓" : "✗"} {name}</p>)}
        {r.calls.map(c => <p className="receipt-call" key={c.id}>{c.operation} · {c.model} · {c.status} · {c.total_tokens ?? "미확인"} tokens</p>)}
      </details>}
      <p className="receipt-note">영수증을 다시 열거나 인쇄해도 모델을 호출하거나 성공 기록을 추가하지 않습니다.</p>
    </article>
  </div>, document.body);
}
