import type { Receipt as ReceiptData } from "../types";

export function Receipt({ receipt: r, onRefresh }: { receipt: ReceiptData; onRefresh: () => Promise<void> }) {
  return <article className="receipt-paper" aria-label="성공 영수증">
    <div className="receipt-actions">
      <button onClick={() => window.print()}>인쇄 / PDF 저장</button>
      <button onClick={onRefresh}>새로고침</button>
    </div>
    <p className="receipt-eyebrow">AI SLOP STOP CHALLENGE</p>
    <h1>PASS 영수증</h1>
    {r.source === "mock" ? <p className="receipt-warning">샘플 영수증</p>
      : !r.verified && <p className="receipt-warning">기록 확인 필요</p>}
    <dl>
      <dt>성공 시각</dt><dd>{new Date(r.created_at).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" })}</dd>
      <dt>승인 방식</dt><dd>{r.pass_source === "staff" ? "STAFF 수동 승인" : "AI 판정"}</dd>
    </dl>
    <img className="receipt-image" src={r.image_url} alt="PASS 받은 생성 이미지" />
    <h2>성공 프롬프트</h2>
    <pre>{r.prompt}</pre>
    {r.pass_source === "router" && r.router_reason && <>
      <h2>통과 이유</h2>
      <p className="receipt-reason">{r.router_reason}</p>
    </>}
    <h2>성공까지 누적 사용량</h2>
    <dl>
      <dt>이미지 생성 요청</dt><dd>{r.generation_calls.toLocaleString("ko-KR")}회</dd>
      <dt>판정 요청</dt><dd>{r.router_calls.toLocaleString("ko-KR")}회</dd>
      <dt>{r.usage_complete ? "총 사용 토큰" : "확인된 사용 토큰"}</dt>
      <dd>{r.known_tokens_total.toLocaleString("ko-KR")}</dd>
    </dl>
    {Object.keys(r.tokens_by_model).length > 0 && <>
      <h2>모델별 사용 토큰</h2>
      {Object.entries(r.tokens_by_model)
        .sort(([a, au], [b, bu]) => Number(bu.generation_calls > 0) - Number(au.generation_calls > 0) || a.localeCompare(b))
        .map(([model, usage]) => <dl className="receipt-token-model" key={model}>
          <dt>
            {usage.generation_calls > 0 ? (usage.router_calls > 0 ? "생성·판정" : "이미지 생성") : "AI 판정"}
            <span className="receipt-model-name">{model.replace(/^[^/]+\//, "")}</span>
          </dt>
          <dd>{typeof usage.known_total_tokens === "number" ? usage.known_total_tokens.toLocaleString("ko-KR") : "미확인"}
            {usage.calls_without_total_tokens > 0 && <span className="receipt-model-name">확인된 합계</span>}
          </dd>
        </dl>)}
    </>}
    {!r.usage_complete && <p className="receipt-note">일부 사용량 미확인</p>}
  </article>;
}
