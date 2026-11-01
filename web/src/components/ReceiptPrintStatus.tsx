import { useEffect, useState } from "react";
import { printState, type PrintState } from "../api/printing";
const labels: Record<string, string> = {
  PENDING: "출력 대기 · 맥북 출력 프로그램이 켜져 있어야 합니다.",
  RENDERING: "Atkinson 영수증 준비 중…",
  SENDING: "프린터로 전송 중…",
  SENT: "맥북 인쇄 큐에 전달했습니다. 종이 출력을 확인해 주세요.",
  FAILED: "출력 준비 실패 · 운영자에게 재출력을 요청해 주세요.",
  UNKNOWN: "출력 여부 확인 필요 · 중복 방지를 위해 자동 재전송하지 않습니다.",
};
export function ReceiptPrintStatus({ challengeId, accessToken }: { challengeId: string; accessToken: string }) {
  const [state, setState] = useState<PrintState>();
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    const poll = async () => {
      let again = true;
      const deadline = setTimeout(() => controller.abort(), 15_000);
      try {
        const next = await printState(challengeId, accessToken, controller.signal);
        if (!active) return;
        setState(next); setError("");
        again = next.enabled && !["SENT", "FAILED", "UNKNOWN"].includes(next.job?.status ?? "");
      } catch { if (active) setError("출력 상태 확인 실패. 저장된 영수증은 유지됩니다."); }
      finally { clearTimeout(deadline); }
      if (active && again && !controller.signal.aborted) timer = setTimeout(() => void poll(), 5000);
    };
    void poll();
    return () => { active = false; controller.abort(); if (timer) clearTimeout(timer); };
  }, [challengeId, accessToken, retry]);
  if (error) return <div className="receipt-device-status" role="status">{error} <button onClick={() => setRetry(n => n + 1)}>상태 다시 확인</button></div>;
  if (!state?.enabled) return null;
  return <p className="receipt-device-status" role="status">PT-210 · {labels[state.job?.status ?? "PENDING"] ?? "출력 상태 확인 중…"}</p>;
}
