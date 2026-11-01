import { useState } from "react";
import { clearPairing, pairPrinter, pairingToken } from "../api/printing";
export function PrintStation() {
  const [enabled, setEnabled] = useState(() => !!pairingToken());
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const connect = async () => {
    setBusy(true); setError("");
    try { await pairPrinter(code.trim()); setEnabled(true); setCode(""); }
    catch (e) { setError(e instanceof Error ? e.message : "연결 실패"); }
    finally { setBusy(false); }
  };
  return <details className="print-station">
    <summary>행사 프린터 {enabled ? "ON" : "OFF"}</summary>
    <p>연결 후 새로 시작하는 챌린지의 PASS 영수증을 PT-210으로 자동 출력합니다.</p>
    {enabled ? <button onClick={() => { clearPairing(); setEnabled(false); }}>새 챌린지 자동 출력 끄기</button> : <>
      <label>행사 연결 코드<input type="password" autoComplete="off" value={code}
        onChange={e => setCode(e.target.value)} /></label>
      <button disabled={busy || !code.trim()} onClick={() => void connect()}>{busy ? "확인 중…" : "연결"}</button>
    </>}
    <p>현재 탭에만 적용됩니다. 이미 시작한 챌린지의 출력 설정은 유지됩니다.</p>
    {error && <p role="alert">{error}</p>}
  </details>;
}
