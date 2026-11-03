// 목표 이미지 챌린지 UI의 상태 모델.
// 화면 상태는 브라우저, 실제 사용량/성공 영수증은 백엔드와 Supabase가 관리.

export type Verdict = "PASS" | "FAIL";
export type EvaluationCriteria = Record<"subject" | "composition" | "color" | "background", "MATCH" | "MISMATCH">;

export interface EnergySummary {
  scope: "gpu_board_generation_window";
  energy_j: number | null;
  energy_wh: number | null;
  measured_generation_calls: number;
  unmeasured_generation_calls: number;
  unmeasured_router_calls: number;
  measured_router_calls?: number;
  router_energy_wh?: number | null;
  total_gpu_energy_wh?: number | null;
  generation_complete: boolean;
}

export interface GenerationResult { imageUrl: string; energy?: EnergySummary }

export interface ClipSimilarity {
  cosine?: number;
  model?: string;
  pipeline?: string;
  status?: string;
  error?: string;
  elapsed_ms?: number;
  roundtrip_ms: number;
}

export interface Receipt {
  campaign?: { id: string; participant_code: string; energy_basis: "tokens_only" | "measured_gpu" | "unavailable"; target_wh: number | null; reference_target_wh?: number | null; reference_target_source?: string | null; multiplier: number; state: string; profile_snapshot: Record<string,unknown> } | null;
  bike?: { id: string; campaign_energy_wh: number; multiplier: number; target_wh: number | null; progress_pct: number | null; target_reached: boolean; elapsed_s: number; measured_s: number; gap_count: number; mode: string; end_reason: string } | null;
  energy?: EnergySummary;
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
  average_prompt_length?: number | null;
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
  energy?: EnergySummary;
  id: string;
  prompt: string;
  imageUrl?: string; // 생성 이미지 (data URL / blob URL)
  genStatus: "generating" | "ready" | "error";
  genError?: string;

  // 평가(Try) 결과
  evalStatus: "none" | "evaluating" | "done" | "error";
  criteria?: EvaluationCriteria; // Gemini의 항목별 정성 평가
  routerModel?: string;
  similarity?: ClipSimilarity;
  verdict?: Verdict; // Router 최종 판정 (또는 STAFF 수동 전환)
  reason?: string; // Router 판정 이유
  evalError?: string;
  staffOverride?: boolean; // STAFF 가 수동으로 PASS 전환했는지
  receiptError?: string;
  receiptPending?: boolean;
  receiptSource?: "router" | "staff";
}

// 목표 이미지 1장에 대한 하나의 챌린지. 시도들을 누적.
export interface ModelProfile {
  id: string;
  label: string;
  ready: boolean;
  image_model: string;
  router_model: string;
  energy_basis: "tokens_only" | "measured_gpu";
}

export interface ProfileSnapshot {
  profile_id: string;
  image_model: string;
  router_model: string;
}

export interface Challenge {
  profile?: ProfileSnapshot;
  id: string;
  accessToken: string;
  title: string;
  targetImageUrl: string; // 기본(정적) 또는 업로드본
  isCustomTarget: boolean; // 사용자가 올린 목표 이미지인지
  attempts: Attempt[];
  updatedAt: number;
  receipt?: Receipt;
}
