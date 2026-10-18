import { useState } from "react";

export function StaffApproval({ onApprove, onClose }: {
  onApprove: (staff: {token: string; actor: string; reason: string}) => Promise<void>;
  onClose: () => void;
}) {
  const [token, setToken] = useState("");
  const [actor, setActor] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return <div className="modal-scrim"><form className="modal staff-approval" role="dialog" aria-modal="true" aria-label="STAFF 승인"
    onSubmit={async e => { e.preventDefault(); setBusy(true); setError("");
      try { await onApprove({ token, actor, reason }); onClose(); }
      catch (err) { setError(err instanceof Error ? err.message : "STAFF 승인 실패"); }
      finally { setBusy(false); }
    }}>
    <h2>STAFF 수동 PASS</h2>
    <label>운영자 인증 토큰<input autoFocus type="password" value={token} onChange={e => setToken(e.target.value)} required autoComplete="off" /></label>
    <label>운영자 이름<input value={actor} onChange={e => setActor(e.target.value)} required maxLength={100} /></label>
    <label>승인 사유<textarea value={reason} onChange={e => setReason(e.target.value)} required maxLength={1000} /></label>
    {error && <p role="alert">{error}</p>}
    <div className="modal__actions"><button type="button" onClick={onClose} disabled={busy}>취소</button>
      <button className="btn-primary" disabled={busy}>{busy ? "저장 확인 중…" : "PASS 저장 및 영수증 발급"}</button></div>
  </form></div>;
}
