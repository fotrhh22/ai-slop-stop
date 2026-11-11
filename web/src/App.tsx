import { useEffect, useMemo, useState } from "react";
import { Sidebar } from "./components/Sidebar";
import { Chat } from "./components/Chat";
import { Composer } from "./components/Composer";
import { TargetImagePicker } from "./components/TargetImagePicker";
import { MenuIcon } from "./components/icons";
import type { Attempt, Challenge, ProfileSnapshot } from "./types";
import { createChallenge, generateImage, evaluate, finalize, fetchReceipt } from "./api/client";
import type { ChallengeTarget } from "./data/challengeTargets";

const uid = () => crypto.randomUUID();

// 선택한 카탈로그 이미지 원본을 data URL로 저장한 뒤 챌린지를 만든다.
function newChallenge(targetImageUrl: string, id: string, accessToken: string, target: ChallengeTarget, profile?: ProfileSnapshot): Challenge {
  return {
    id,
    accessToken,
    profile,
    title: `${target.id} · ${target.title}`,
    targetId: target.id,
    targetImageUrl,
    isCustomTarget: false,
    attempts: [],
    updatedAt: Date.now(),
  };
}

export default function App() {
  const [challenges, setChallenges] = useState<Challenge[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  // 새 세션의 모델 선택 → 이미지 선택 모달.
  const [showPicker, setShowPicker] = useState(false);
  const [staffMode, setStaffMode] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
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

  // 새 세션은 모델 선택 → 고정 목표 이미지 선택 순서로 진행한다.
  const startNewChallenge = () => {
    if (creating) return;
    setShowPicker(true);
    setSidebarOpen(false);
  };

  // 모달에서 목표 이미지 확정 -> 그제서야 챌린지 생성 + 활성화
  const confirmTarget = async (dataUrl: string, profileId: string, target: ChallengeTarget) => {
    if (creating) return;
    setCreating(true);
    setError("");
    try {
      const remote = await createChallenge(dataUrl, profileId);
      const c = newChallenge(dataUrl, remote.id, remote.accessToken, target, remote.profile);
      setChallenges((prev) => [c, ...prev]);
      setActiveId(c.id);
      setShowPicker(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "세션 생성 실패");
      throw e;
    }
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
      const { imageUrl, energy } = await generateImage(prompt, active, aid);
      patchAttempt(cid, aid, (a) => ({ ...a, genStatus: "ready", imageUrl, energy }));
    } catch (e) {
      patchAttempt(cid, aid, (a) => ({
        ...a,
        genStatus: "error",
        genError: e instanceof Error && e.message === "GENERATE_TIMEOUT"
          ? "이미지 생성 대기 시간이 초과되었습니다."
          : e instanceof Error ? e.message : "GENERATE_FAILED",
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
    patchAttempt(cid, aid, (x) => ({ ...x, evalStatus: "evaluating", evalError: undefined, similarity: undefined }));
    try {
      const r = await evaluate(active, a);
      patchAttempt(cid, aid, (x) => ({
        ...x,
        evalStatus: "done",
        criteria: r.criteria,
        routerModel: r.router_model ?? undefined,
        similarity: r.similarity,
        verdict: r.verdict,
        reason: r.reason,
        staffOverride: false,
        receiptError: r.receipt_error,
        receiptPending: r.receipt_pending,
      }));
      if (r.receipt) {
        patch(cid, c => ({ ...c, receipt: r.receipt }));
      } else if (r.verdict === "PASS" && r.receipt_pending) {
        // Render the persisted verdict before waiting for Storage/receipt readback.
        try {
          const saved = await finalize(active, a);
          patch(cid, c => ({ ...c, receipt: saved }));
          patchAttempt(cid, aid, x => ({ ...x, receiptPending: false }));
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

  const saveReceipt = async (aid: string, staff = false) => {
    if (!active || busy) return;
    const cid = active.id;
    const attempt = active.attempts.find(a => a.id === aid);
    if (!attempt) return;
    patchAttempt(cid, aid, a => ({ ...a, receiptPending: true, receiptError: undefined, receiptSource: staff ? "staff" : "router" }));
    try {
      const saved = await finalize(active, attempt, staff);
      patch(cid, c => ({ ...c, receipt: saved }));
      patchAttempt(cid, aid, a => ({ ...a, verdict: "PASS", staffOverride: saved.pass_source === "staff",
        evalStatus: "done", receiptPending: false }));
    } catch (e) {
      patchAttempt(cid, aid, a => ({ ...a, receiptPending: false,
        receiptError: e instanceof Error ? e.message : "영수증 저장 실패" }));
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
          <button className={"staff-toggle" + (staffMode ? " is-on" : "")}
            onClick={() => setStaffMode(v => !v)} title="STAFF 모드: 수동 PASS 전환 허용">
            STAFF {staffMode ? "ON" : "OFF"}
          </button>
        </div>

        {active?.profile && <p className="workflow-banner">
          {active.profile.profile_id === "gemini" ? "Gemini · 토큰 기록" : `${active.profile.profile_id} · GPU 실측 Wh`}
          <br />생성: {active.profile.image_model} · 판정: {active.profile.router_model}
        </p>}
        {creating && <p className="workflow-banner" role="status">모델 연결 확인 · 목표 이미지와 세션을 저장하는 중…</p>}
        {error && <p className="workflow-banner is-error" role="alert">{error}</p>}
        {active?.receipt && <div className="workflow-banner">PASS 저장 완료 · 영수증은 채팅 아래에 표시됩니다.</div>}

        {active ? (
          <>
            {/* 목표 이미지 변경 = 새 챌린지(모달)로 처리 */}
            <Chat
              challenge={active}
              busy={busy || completed}
              staffMode={staffMode}
              onUploadTarget={startNewChallenge}
              onTry={onTry}
              onStaffPass={aid => { if (staffMode) void saveReceipt(aid, true); }}
              onReceiptRetry={aid => { void saveReceipt(aid, active.attempts.find(a => a.id === aid)?.receiptSource === "staff"); }}
              onReceiptRefresh={async () => {
                const cid = active.id;
                try {
                  const saved = await fetchReceipt(active);
                  patch(cid, c => ({ ...c, receipt: saved }));
                } catch (e) {
                  setError(e instanceof Error ? e.message : "영수증 조회 실패");
                }
              }}
            />
            <Composer disabled={busy || completed || creating} onSend={onSend} />
          </>
        ) : (
          <div className="chat__scroll">
            <div className="chat__empty">
              <div className="chat__empty-badge">AI SLOP STOP CHALLENGE</div>
              <h1>목표 이미지를 재현하는 챌린지</h1>
              <p>
                모델과 목표 이미지 9개 중 하나를 선택하고,
                프롬프트로 최대한 비슷하게 재현해보세요.
              </p>
              <button className="btn-primary" onClick={startNewChallenge}>
                + 새 세션 시작
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
    </div>
  );
}
