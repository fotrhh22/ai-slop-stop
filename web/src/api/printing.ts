const BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/+$/, "");
const KEY = "ai-slop-print-pairing";
export function pairingToken(): string { try { return sessionStorage.getItem(KEY) ?? ""; } catch { return ""; } }
export function clearPairing() { try { sessionStorage.removeItem(KEY); } catch { /* no persistent pairing */ } }
export async function pairPrinter(token: string): Promise<string> {
  if ((import.meta.env.VITE_USE_MOCK ?? "true") !== "false") throw new Error("실제 API 모드에서 연결해 주세요.");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 20_000);
  try {
    const res = await fetch(`${BASE}/api/printing/pair`, { method: "POST",
      headers: { "X-Print-Station-Token": token }, signal: controller.signal, cache: "no-store" });
    if (!res.ok) throw new Error("행사 연결 실패: 서버 설정·연결 코드·SQL 적용을 확인해 주세요.");
    const data = await res.json();
    sessionStorage.setItem(KEY, token);
    return data.station_id;
  } finally { clearTimeout(timer); }
}
export interface PrintState { enabled: boolean; job: null | { id: string; status: string; error_code?: string; cups_job_id?: string }; }
export async function printState(challengeId: string, accessToken: string, signal: AbortSignal): Promise<PrintState> {
  const res = await fetch(`${BASE}/api/challenges/${challengeId}/print-job`, {
    headers: { "X-Challenge-Token": accessToken }, signal, cache: "no-store" });
  if (!res.ok) throw new Error("출력 상태 확인 실패. 영수증 저장은 완료되었습니다.");
  return res.json();
}
