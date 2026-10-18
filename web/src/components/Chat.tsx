import { useEffect, useRef } from "react";
import type { Challenge } from "../types";
import { AttemptCard } from "./AttemptCard";
import { TargetImagePanel } from "./TargetImagePanel";

interface Props {
  challenge: Challenge;
  busy: boolean;
  staffMode: boolean;
  onUploadTarget: (dataUrl: string) => void;
  onTry: (attemptId: string) => void;
  onStaffPass: (attemptId: string) => void;
  onReceiptRetry: (attemptId: string) => void;
}

export function Chat({
  challenge,
  busy,
  staffMode,
  onUploadTarget,
  onTry,
  onStaffPass,
  onReceiptRetry,
}: Props) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [challenge.attempts.length, busy]);

  return (
    <div className="chat__scroll">
      <TargetImagePanel
        targetImageUrl={challenge.targetImageUrl}
        isCustom={challenge.isCustomTarget}
        onUpload={onUploadTarget}
      />

      {challenge.attempts.length === 0 ? (
        <div className="chat__empty">
          <h1>프롬프트로 목표 이미지를 재현해보세요</h1>
          <p>아래 입력창에 이미지를 설명하는 프롬프트를 입력하고 생성하세요.</p>
        </div>
      ) : (
        <div className="attempt-list">
          {challenge.attempts.map((a) => (
            <AttemptCard
              key={a.id}
              attempt={a}
              busy={busy}
              staffMode={staffMode}
              onTry={onTry}
              onStaffPass={onStaffPass}
              onReceiptRetry={onReceiptRetry}
            />
          ))}
          <div ref={endRef} />
        </div>
      )}
    </div>
  );
}
