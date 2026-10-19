// 목표 이미지 챌린지 UI의 상태 모델.
// 화면 상태는 브라우저, 실제 사용량/성공 영수증은 백엔드와 Supabase가 관리.

export type Verdict = "PASS" | "FAIL";
export type EvaluationCriteria = Record<"subject" | "composition" | "color" | "background", "MATCH" | "MISMATCH">;

export interface Receipt {
  id: string;
  challenge_id: string;
  attempt_id: string;
  prompt: string;
  image_url: string;
  pass_source: "router" | "staff";
  staff_actor?: string;
  staff_reason?: string;
  router_verdict?: Verdict;
  router_reason?: string | null;
  created_at: string;
  generation_calls: number;
  router_calls: number;
  model_calls_total: number;
  failed_calls: number;
  known_tokens_total: number;
  calls_without_total_tokens: number;
  tokens_by_model: Record<string, Record<string, number>>;
  source: "supabase" | "mock";
  verified: boolean;
  usage_complete: boolean;
  verification: Record<string, boolean>;
  calls: { id: string; operation: string; model: string; status: string; total_tokens: number | null }[];
}

// 생성 이미지 1장 + 그에 대한 평가 결과 = 1 Attempt
export interface Attempt {
  id: string;
  prompt: string;
  imageUrl?: string; // 생성 이미지 (data URL / blob URL)
  genStatus: "generating" | "ready" | "error";
  genError?: string;

  // 평가(Try) 결과
  evalStatus: "none" | "evaluating" | "done" | "error";
  criteria?: EvaluationCriteria; // Gemini의 항목별 정성 평가
  routerModel?: string;
  verdict?: Verdict; // Router 최종 판정 (또는 STAFF 수동 전환)
  reason?: string; // Router 판정 이유
  evalError?: string;
  staffOverride?: boolean; // STAFF 가 수동으로 PASS 전환했는지
  receiptError?: string;
  receiptPending?: boolean;
}

// 목표 이미지 1장에 대한 하나의 챌린지. 시도들을 누적.
export interface Challenge {
  id: string;
  accessToken: string;
  title: string;
  targetImageUrl: string; // 기본(정적) 또는 업로드본
  isCustomTarget: boolean; // 사용자가 올린 목표 이미지인지
  attempts: Attempt[];
  updatedAt: number;
  receipt?: Receipt;
}
