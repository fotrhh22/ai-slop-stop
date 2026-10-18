// 목표 이미지 챌린지 UI의 상태 모델.
// 서버/DB 저장 없음 — 전부 브라우저 메모리.

export type Verdict = "PASS" | "FAIL";

// 생성 이미지 1장 + 그에 대한 평가 결과 = 1 Attempt
export interface Attempt {
  id: string;
  prompt: string;
  imageUrl?: string; // 생성 이미지 (data URL / blob URL)
  genStatus: "generating" | "ready" | "error";
  genError?: string;

  // 평가(Try) 결과
  evalStatus: "none" | "evaluating" | "done" | "error";
  similarity?: number; // 표시용 재스케일 점수 (예: 0.47). 일치율 %가 아님.
  similarityRaw?: number; // 원시 CLIP 코사인 (투명성)
  verdict?: Verdict; // Router 최종 판정 (또는 STAFF 수동 전환)
  reason?: string; // Router 판정 이유
  evalError?: string;
  staffOverride?: boolean; // STAFF 가 수동으로 PASS 전환했는지
}

// 목표 이미지 1장에 대한 하나의 챌린지. 시도들을 누적.
export interface Challenge {
  id: string;
  title: string;
  targetImageUrl: string; // 기본(정적) 또는 업로드본
  isCustomTarget: boolean; // 사용자가 올린 목표 이미지인지
  attempts: Attempt[];
  updatedAt: number;
}
