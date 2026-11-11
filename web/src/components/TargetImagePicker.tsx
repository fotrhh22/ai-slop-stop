import { useEffect, useRef, useState } from "react";
import { fetchModelProfiles } from "../api/client";
import { CHALLENGE_TARGETS, loadTargetDataUrl, type ChallengeTarget } from "../data/challengeTargets";
import type { ModelProfile } from "../types";

interface Props {
  mode: "settings" | "target";
  selectedProfileId: string | null;
  onSaveModel: (profileId: string) => void;
  onSettings: () => void;
  onCancel: () => void;
  onConfirm: (dataUrl: string, profileId: string, target: ChallengeTarget) => Promise<void>;
}

export function TargetImagePicker({ mode, selectedProfileId, onSaveModel, onSettings, onCancel, onConfirm }: Props) {
  const [profiles, setProfiles] = useState<ModelProfile[]>([]);
  const [profileId, setProfileId] = useState(selectedProfileId ?? "gemini");
  const [profileError, setProfileError] = useState("");
  const [error, setError] = useState("");
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const selecting = useRef(false);
  const dialog = useRef<HTMLDivElement>(null);
  const selected = profiles.find(p => p.id === profileId);

  useEffect(() => {
    let alive = true;
    const refresh = async () => {
      try {
        const rows = await fetchModelProfiles();
        if (alive) { setProfiles(rows); setProfileError(""); }
      } catch { if (alive) setProfileError("모델 연결 상태를 확인하지 못했습니다. 다시 확인해 주세요."); }
    };
    void refresh();
    const timer = setInterval(refresh, 30_000);
    return () => { alive = false; clearInterval(timer); };
  }, [retry]);

  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog.current?.focus();
    return () => previous?.focus();
  }, []);

  const choose = async (target: ChallengeTarget) => {
    if (selecting.current) return;
    if (!selected?.ready || profileError) {
      setError("환경설정에서 사용 가능한 모델을 선택해 주세요."); return;
    }
    selecting.current = true; setPendingId(target.id); setError("");
    try {
      const dataUrl = await loadTargetDataUrl(target);
      await onConfirm(dataUrl, profileId, target);
    } catch (e) { setError(e instanceof Error ? e.message : "세션을 만들지 못했습니다. 다시 선택해 주세요."); }
    finally { selecting.current = false; setPendingId(null); }
  };

  return (
    <div className="modal-scrim" onClick={() => { if (!selecting.current) onCancel(); }}>
      <div ref={dialog} className={`modal session-picker${mode === "target" ? " session-picker--targets" : ""}`}
        role="dialog" aria-modal="true" aria-labelledby="session-picker-title" tabIndex={-1}
        onClick={e => e.stopPropagation()} onKeyDown={e => {
          if (e.key === "Escape" && !selecting.current) { e.stopPropagation(); onCancel(); }
          if (e.key === "Tab") {
            const controls = dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), [href], input:not(:disabled), [tabindex="0"]');
            if (!controls?.length) { e.preventDefault(); return; }
            const first = controls[0], last = controls[controls.length - 1];
            if (e.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) { e.preventDefault(); last.focus(); }
            else if (!e.shiftKey && (document.activeElement === last || document.activeElement === dialog.current)) { e.preventDefault(); first.focus(); }
          }
        }}>
        <h2 id="session-picker-title" className="modal__title">{mode === "settings" ? "환경설정" : "목표 이미지를 골라주세요"}</h2>
        <p className="modal__desc">{mode === "settings" ? "모델을 한 번 선택하면 이 브라우저의 모든 새 세션에 적용됩니다. 새로고침 후에도 유지됩니다." : "이미지를 누르면 저장된 모델로 새 세션이 시작됩니다. 각 테마는 왼쪽부터 하·중·상입니다."}</p>

        {mode === "settings" ? <>
          <div className="session-models" role="radiogroup" aria-label="생성·판정 모델">
            {profiles.map(p => <button key={p.id} type="button" role="radio" aria-checked={p.id === profileId}
              className={`session-model${p.id === profileId ? " is-selected" : ""}`} disabled={!p.ready}
              onClick={() => { setProfileId(p.id); setError(""); }}>
              <strong>{p.label}</strong><span>{p.ready ? "사용 가능" : "준비 안 됨"}</span>
            </button>)}
          </div>
          {!profiles.length && !profileError && <p role="status">모델 연결 상태 확인 중…</p>}
          {selected && <p className="modal__desc">생성: {selected.image_model}<br />판정: {selected.router_model}</p>}
        </> : <>
          <p className="session-picker__model">선택 모델 · {selected?.label ?? profileId}</p>
          <div className="target-catalog" aria-busy={!!pendingId}>
            {(["A", "B", "C"] as const).map(theme => {
              const targets = CHALLENGE_TARGETS.filter(t => t.theme === theme);
              return <section key={theme} className="target-catalog__theme" aria-labelledby={`theme-${theme}`}>
                <h3 id={`theme-${theme}`}>{theme} · {targets[0].themeLabel}</h3>
                <div className="target-catalog__grid">{targets.map(target => <button key={target.id} type="button"
                  className={`target-choice${pendingId === target.id ? " is-selected" : ""}`} disabled={!!pendingId || !selected?.ready || !!profileError}
                  aria-label={`${target.theme} ${target.difficulty} · ${target.title} 선택`} onClick={() => void choose(target)}>
                  <img src={target.imageUrl} alt={target.title} width={1024} height={1024} />
                  <span className="target-choice__caption"><b>{target.difficulty}</b><span>{target.title}</span></span>
                </button>)}</div>
              </section>;
            })}
          </div>
        </>}
        {mode === "target" && !selected?.ready && !profileError && <p role="status">{profiles.length ? "저장된 모델을 사용할 수 없습니다. 환경설정에서 모델을 확인해 주세요." : "모델 연결 상태 확인 중…"}</p>}
        {pendingId && <p role="status">{pendingId} · 목표 이미지와 세션을 저장하는 중…</p>}
        {(profileError || error) && <p className="modal__error" role="alert">{error || profileError}</p>}
        <div className="modal__actions">
          <button className="btn-secondary" disabled={!!pendingId} onClick={onCancel}>취소</button>
          {profileError && <button className="btn-secondary" onClick={() => setRetry(v => v + 1)}>연결 다시 확인</button>}
          {mode === "target" ? <button className="btn-secondary" disabled={!!pendingId} onClick={onSettings}>환경설정</button>
            : <>
              <button className="btn-primary" disabled={!selected?.ready || !!profileError} onClick={() => onSaveModel(profileId)}>저장</button></>}
        </div>
      </div>
    </div>
  );
}
