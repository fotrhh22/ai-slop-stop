import type { Attempt, Challenge, Receipt, Verdict, EvaluationCriteria } from "../types";

const USE_MOCK = (import.meta.env.VITE_USE_MOCK ?? "true") !== "false";
const API_BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/+$/, "");

export interface EvaluateResult {
  criteria?: EvaluationCriteria;
  router_model?: string;
  verdict: Verdict;
  reason: string;
  receipt?: Receipt;
  receipt_error?: string;
  receipt_pending?: boolean;
}

class ApiError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
}

async function req<T>(path: string, body?: unknown, token?: string, timeoutMs?: number): Promise<T> {
  const controller = new AbortController();
  const timer = timeoutMs ? setTimeout(() => controller.abort(), timeoutMs) : undefined;
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      method: body === undefined ? "GET" : "POST",
      headers: { "Content-Type": "application/json", ...(token ? { "X-Challenge-Token": token } : {}) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      signal: controller.signal,
      cache: "no-store",
    });
    if (!res.ok) {
      let code = `HTTP_${res.status}`;
      try { code = (await res.json())?.detail ?? code; } catch { /* use status */ }
      throw new ApiError(typeof code === "string" ? code : "INVALID_REQUEST", res.status);
    }
    return await res.json();
  } catch (e) {
    if (e instanceof TypeError || (e instanceof DOMException && e.name === "AbortError")) {
      throw new ApiError("API_CONNECTION_FAILED", 0);
    }
    throw e;
  } finally {
    if (timer) clearTimeout(timer);
  }
}

interface EvaluationJob {
  job_id: string;
  status: "running" | "done" | "error";
  result?: EvaluateResult;
  error?: string;
}

function transient(e: unknown) {
  return e instanceof ApiError && (e.status === 0 || e.status >= 500) && e.message !== "EVALUATION_BUSY";
}

function evaluationError(code: string) {
  const messages: Record<string, string> = {
    ROUTER_TIMEOUT: "Gemini 판정 응답 대기 시간이 초과되었습니다. 다시 Try해 주세요.",
    ROUTER_FAILED: "Gemini 판정 요청에 실패했습니다. 다시 Try해 주세요.",
    EVALUATION_BUSY: "판정 요청이 몰려 있습니다. 잠시 후 다시 Try해 주세요.",
    EVALUATION_INTERRUPTED: "서버가 재시작되어 판정 상태를 확인할 수 없습니다. 다시 Try해 주세요.",
    API_CONNECTION_FAILED: "서버 연결이 끊겨 판정 상태를 확인하지 못했습니다. 다시 Try해 주세요.",
  };
  return new Error(messages[code] ?? code);
}

async function waitForEvaluation(challenge: Challenge, attempt: Attempt): Promise<EvaluateResult> {
  // A retry of the short start request reuses this ID, never a new paid call.
  const jobId = crypto.randomUUID();
  let job: EvaluationJob | undefined;
  for (let i = 0; i < 3; i++) {
    try {
      job = await req<EvaluationJob>(`/api/challenges/${challenge.id}/evaluations`, {
        job_id: jobId, attempt_id: attempt.id, generated_image: attempt.imageUrl,
      }, challenge.accessToken, 20_000);
      break;
    } catch (e) {
      if (!transient(e) || i === 2) throw evaluationError(e instanceof Error ? e.message : "EVALUATE_FAILED");
      await sleep(1000 * (i + 1));
    }
  }
  if (!job) throw evaluationError("API_CONNECTION_FAILED");
  const deadline = Date.now() + 180_000;
  let missed = 0;
  while (Date.now() < deadline) {
    if (job.status === "done" && job.result) return job.result;
    if (job.status === "error") throw evaluationError(job.error ?? "EVALUATE_FAILED");
    await sleep(2000);
    try {
      job = await req<EvaluationJob>(`/api/challenges/${challenge.id}/evaluations/${job.job_id}`,
        undefined, challenge.accessToken, 15_000);
      missed = 0;
    } catch (e) {
      if (!transient(e) || ++missed >= 5) throw evaluationError(e instanceof Error ? e.message : "EVALUATE_FAILED");
    }
  }
  throw evaluationError("ROUTER_TIMEOUT");
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const samples = new Map<string, { generates: number; routes: number; receipt?: Receipt }>();

export async function createChallenge(targetImage: string): Promise<{id: string; accessToken: string}> {
  if (!USE_MOCK) return req("/api/challenges", { target_image: targetImage });
  const id = crypto.randomUUID();
  samples.set(id, { generates: 0, routes: 0 });
  return { id, accessToken: "mock" };
}

export async function generateImage(prompt: string, challenge: Challenge, attemptId: string): Promise<{imageUrl: string}> {
  if (USE_MOCK) {
    await sleep(500);
    const s = samples.get(challenge.id)!;
    s.generates += 1;
    return { imageUrl: `https://picsum.photos/seed/${encodeURIComponent(attemptId)}/512/512` };
  }
  return req(`/api/challenges/${challenge.id}/generate`, { prompt, attempt_id: attemptId }, challenge.accessToken);
}

function sampleReceipt(challenge: Challenge, attempt: Attempt, source: "router" | "staff"): Receipt {
  const s = samples.get(challenge.id)!;
  if (s.receipt) return s.receipt;
  s.receipt = { id: crypto.randomUUID(), challenge_id: challenge.id, attempt_id: attempt.id,
    prompt: attempt.prompt, image_url: attempt.imageUrl!, pass_source: source,
    router_verdict: source === "router" ? "PASS" : attempt.verdict,
    created_at: new Date().toISOString(), generation_calls: s.generates, router_calls: s.routes,
    model_calls_total: s.generates + s.routes, failed_calls: 0, known_tokens_total: 0,
    calls_without_total_tokens: s.generates + s.routes, tokens_by_model: {},
    source: "mock", verified: false, usage_complete: false, verification: {}, calls: [] };
  return s.receipt;
}

export async function evaluate(challenge: Challenge, attempt: Attempt): Promise<EvaluateResult> {
  if (USE_MOCK) {
    await sleep(500);
    samples.get(challenge.id)!.routes += 1;
    const verdict = Math.random() >= 0.5 ? "PASS" : "FAIL";
    return { verdict, criteria: { subject: "MATCH", composition: verdict === "PASS" ? "MATCH" : "MISMATCH",
      color: "MATCH", background: verdict === "PASS" ? "MATCH" : "MISMATCH" },
      reason: verdict === "PASS" ? "주요 피사체와 구도가 충분히 유사합니다." : "배경과 구도를 조정해 보세요.",
      router_model: "Mock",
      ...(verdict === "PASS" ? { receipt: sampleReceipt(challenge, attempt, "router") } : {}) };
  }
  return waitForEvaluation(challenge, attempt);
}

export async function finalize(challenge: Challenge, attempt: Attempt,
  staff = false): Promise<Receipt> {
  if (USE_MOCK) return sampleReceipt(challenge, attempt, staff ? "staff" : "router");
  return req(`/api/challenges/${challenge.id}/finalize`, { attempt_id: attempt.id,
    generated_image: attempt.imageUrl, source: staff ? "staff" : "router" }, challenge.accessToken);
}

export async function fetchReceipt(challenge: Challenge): Promise<Receipt> {
  if (USE_MOCK) return samples.get(challenge.id)!.receipt!;
  return req(`/api/challenges/${challenge.id}/receipt`, undefined, challenge.accessToken);
}
