import { useEffect, useMemo, useState } from "react";
import { Sidebar } from "./components/Sidebar";
import { Chat } from "./components/Chat";
import { Composer } from "./components/Composer";
import { TargetImagePicker } from "./components/TargetImagePicker";
import { MenuIcon } from "./components/icons";
import type { Attempt, Challenge, Receipt as ReceiptData } from "./types";
import { createChallenge, generateImage, evaluate, finalize, fetchReceipt } from "./api/client";
import { Receipt } from "./components/Receipt";
import { StaffApproval } from "./components/StaffApproval";

const uid = () => crypto.randomUUID();

// 목표 이미지(data URL)를 받아야만 챌린지를 만든다. 기본 이미지는 없음.
function newChallenge(targetImageUrl: string, id: string, accessToken: string): Challenge {
  return {
    id,
    accessToken,
    title: "새 챌린지",
    targetImageUrl,
    isCustomTarget: true,
    attempts: [],
    updatedAt: Date.now(),
  };
}

export default function App() {
  const [challenges, setChallenges] = useState<Challenge[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  // 목표 이미지 선택 모달 표시 여부 (새 챌린지 시작 시 필수 선행 단계)
  const [showPicker, setShowPicker] = useState(false);
  // STAFF 모드: 수동 PASS 전환 버튼 노출 (운영자 전용)
  const [staffMode, setStaffMode] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const [receipt, setReceipt] = useState<ReceiptData | null>(null);
  const [staffAttempt, setStaffAttempt] = useState<string | null>(null);

  const active = useMemo(
    () => challenges.find((c) => c.id === activeId) ?? null,
    [challenges, activeId]
  );

  const sorted = useMemo(
    () => [...challenges].sort((a, b) => b.updatedAt - a.updatedAt),
    [challenges]
  );

  const busy = useMemo(
    () =>
      !!active &&
      active.attempts.some(
        (a) => a.genStatus === "generating" || a.evalStatus === "evaluating" || a.receiptPending
      ),
    [active]
  );
  const completed = !!active?.receipt || !!active?.attempts.some(a => a.verdict === "PASS");

  useEffect(() => {
    if (!sidebarOpen) return;
    const onKey = (e: KeyboardEvent) =>
      e.key === "Escape" && setSidebarOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sidebarOpen]);

  const patch = (id: string, fn: (c: Challenge) => Challenge) =>
    setChallenges((prev) => prev.map((c) => (c.id === id ? fn(c) : c)));

  const patchAttempt = (cid: string, aid: string, fn: (a: Attempt) => Attempt) =>
    patch(cid, (c) => ({
      ...c,
      attempts: c.attempts.map((a) => (a.id === aid ? fn(a) : a)),
      updatedAt: Date.now(),
    }));

  // "새 챌린지" = 목표 이미지 선택 모달을 연다 (이미지 설정이 필수 선행)
  const startNewChallenge = () => {
    if (creating) return;
    setShowPicker(true);
    setSidebarOpen(false);
  };

  // 모달에서 목표 이미지 확정 -> 그제서야 챌린지 생성 + 활성화
  const confirmTarget = async (dataUrl: string) => {
    if (creating) return;
    setCreating(true);
    setError("");
    setShowPicker(false);
    try {
      const remote = await createChallenge(dataUrl);
      const c = newChallenge(dataUrl, remote.id, remote.accessToken);
      setChallenges((prev) => [c, ...prev]);
      setActiveId(c.id);
    } catch (e) { setError(e instanceof Error ? e.message : "세션 생성 실패"); }
    finally { setCreating(false); }
  };

  const deleteChallenge = (id: string) =>
    setChallenges((prev) => {
      const next = prev.filter((c) => c.id !== id);
      if (id === activeId) setActiveId(next[0]?.id ?? null);
      return next;
    });

  const renameChallenge = (id: string, title: string) => {
    const t = title.trim();
    if (!t) return;
    patch(id, (c) => ({ ...c, title: t }));
  };

  const runGenerate = async (prompt: string) => {
    if (!active || completed) return;
    const cid = active.id;
    const aid = uid();
    const attempt: Attempt = {
      id: aid,
      prompt,
      genStatus: "generating",
      evalStatus: "none",
    };
    patch(cid, (c) => ({
      ...c,
      title: c.attempts.length === 0 ? prompt.slice(0, 30) : c.title,
      attempts: [...c.attempts, attempt],
      updatedAt: Date.now(),
    }));
    try {
      const { imageUrl } = await generateImage(prompt, active, aid);
      patchAttempt(cid, aid, (a) => ({ ...a, genStatus: "ready", imageUrl }));
    } catch (e) {
      patchAttempt(cid, aid, (a) => ({
        ...a,
        genStatus: "error",
        genError: e instanceof Error ? e.message : "GENERATE_FAILED",
      }));
    }
  };

  const onSend = (text: string) => {
    if (busy || completed) return;
    runGenerate(text);
  };

  const onTry = async (aid: string) => {
    if (!active || busy || completed) return;
    const cid = active.id;
    const a = active.attempts.find((x) => x.id === aid);
    if (!a || a.genStatus !== "ready" || !a.imageUrl) return;
    patchAttempt(cid, aid, (x) => ({ ...x, evalStatus: "evaluating" }));
    try {
      const r = await evaluate(active, a);
      patchAttempt(cid, aid, (x) => ({
        ...x,
        evalStatus: "done",
        criteria: r.criteria,
        routerModel: r.router_model,
        verdict: r.verdict,
        reason: r.reason,
        staffOverride: false,
        receiptError: r.receipt_error,
        receiptPending: r.receipt_pending,
      }));
      if (r.receipt) {
        patch(cid, c => ({ ...c, receipt: r.receipt }));
        setReceipt(r.receipt);
      } else if (r.verdict === "PASS" && r.receipt_pending) {
        // Render the persisted verdict before waiting for Storage/receipt readback.
        try {
          const saved = await finalize(active, a);
          patch(cid, c => ({ ...c, receipt: saved }));
          patchAttempt(cid, aid, x => ({ ...x, receiptPending: false }));
          setReceipt(saved);
        } catch (e) {
          patchAttempt(cid, aid, x => ({ ...x, receiptPending: false,
            receiptError: e instanceof Error ? e.message : "영수증 저장 실패" }));
        }
      }
    } catch (e) {
      patchAttempt(cid, aid, (x) => ({
        ...x,
        evalStatus: "error",
        evalError: e instanceof Error ? e.message : "EVALUATE_FAILED",
      }));
    }
  };

  const saveReceipt = async (aid: string, staff?: {token: string; actor: string; reason: string}) => {
    if (!active || busy) return;
    const cid = active.id;
    const attempt = active.attempts.find(a => a.id === aid);
    if (!attempt) return;
    patchAttempt(cid, aid, a => ({ ...a, receiptPending: true, receiptError: undefined }));
    try {
      const saved = await finalize(active, attempt, staff);
      patch(cid, c => ({ ...c, receipt: saved }));
      patchAttempt(cid, aid, a => ({ ...a, verdict: "PASS", staffOverride: saved.pass_source === "staff",
        evalStatus: "done", receiptPending: false }));
      setReceipt(saved);
    } catch (e) {
      patchAttempt(cid, aid, a => ({ ...a, receiptPending: false,
        receiptError: e instanceof Error ? e.message : "영수증 저장 실패" }));
      if (staff) throw e;
    }
  };

  return (
    <div className={"app" + (sidebarOpen ? " sidebar-open" : "")}>
      <div className="app__scrim" onClick={() => setSidebarOpen(false)} />

      <Sidebar
        challenges={sorted}
        activeId={activeId}
        onSelect={(id) => {
          setActiveId(id);
          setSidebarOpen(false);
        }}
        onNew={startNewChallenge}
        onDelete={deleteChallenge}
        onRename={renameChallenge}
      />

      <div className="chat">
        <div className="chat__header">
          <button
            className="chat__menu-btn"
            aria-label="사이드바 열기"
            onClick={() => setSidebarOpen(true)}
          >
            <MenuIcon />
          </button>
          <span className="chat__title">
            {active?.title ?? "AI SLOP STOP CHALLENGE"}
          </span>
          <button
            className={"staff-toggle" + (staffMode ? " is-on" : "")}
            onClick={() => setStaffMode((v) => !v)}
            title="STAFF 모드: 수동 PASS 전환 허용"
          >
            STAFF {staffMode ? "ON" : "OFF"}
          </button>
        </div>

        {creating && <p className="workflow-banner" role="status">목표 이미지와 세션을 저장하는 중…</p>}
        {error && <p className="workflow-banner is-error" role="alert">{error}</p>}
        {active?.receipt && <div className="workflow-banner">PASS 저장 완료 · 새 챌린지를 시작할 수 있습니다.
          <button onClick={async () => { try { setReceipt(await fetchReceipt(active)); }
            catch (e) { setError(e instanceof Error ? e.message : "영수증 조회 실패"); } }}>영수증 다시 보기</button></div>}

        {active ? (
          <>
            {/* 목표 이미지 변경 = 새 챌린지(모달)로 처리 */}
            <Chat
              challenge={active}
              busy={busy || completed}
              staffMode={staffMode}
              onUploadTarget={startNewChallenge}
              onTry={onTry}
              onStaffPass={setStaffAttempt}
              onReceiptRetry={aid => { void saveReceipt(aid); }}
            />
            <Composer disabled={busy || completed || creating} onSend={onSend} />
          </>
        ) : (
          <div className="chat__scroll">
            <div className="chat__empty">
              <div className="chat__empty-badge">AI SLOP STOP CHALLENGE</div>
              <h1>목표 이미지를 재현하는 챌린지</h1>
              <p>
                목표 이미지를 설정하고, 프롬프트로 최대한 비슷하게 재현해보세요.
                먼저 목표 이미지를 설정해야 챌린지가 시작됩니다.
              </p>
              <button className="btn-primary" onClick={startNewChallenge}>
                + 챌린지 시작 (목표 이미지 설정)
              </button>
            </div>
          </div>
        )}
      </div>

      {showPicker && (
        <TargetImagePicker
          onCancel={() => setShowPicker(false)}
          onConfirm={confirmTarget}
        />
      )}
      {staffAttempt && <StaffApproval onClose={() => setStaffAttempt(null)}
        onApprove={staff => saveReceipt(staffAttempt, staff)} />}
      {receipt && <Receipt receipt={receipt} onClose={() => setReceipt(null)} />}
    </div>
  );
}
