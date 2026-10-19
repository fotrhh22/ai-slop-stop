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

async function req<T>(path: string, body?: unknown, token?: string, staffToken?: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json", ...(token ? { "X-Challenge-Token": token } : {}),
      ...(staffToken ? { "X-Staff-Token": staffToken } : {}) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!res.ok) {
    let code = `HTTP_${res.status}`;
    try { code = (await res.json())?.detail ?? code; } catch { /* use status */ }
    throw new Error(typeof code === "string" ? code : "INVALID_REQUEST");
  }
  return res.json();
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
  return req(`/api/challenges/${challenge.id}/evaluate`, {
    attempt_id: attempt.id, generated_image: attempt.imageUrl, defer_receipt: true,
  }, challenge.accessToken);
}

export async function finalize(challenge: Challenge, attempt: Attempt,
  staff?: { token: string; actor: string; reason: string }): Promise<Receipt> {
  if (USE_MOCK) return sampleReceipt(challenge, attempt, staff ? "staff" : "router");
  return req(`/api/challenges/${challenge.id}/finalize`, { attempt_id: attempt.id,
    generated_image: attempt.imageUrl, source: staff ? "staff" : "router",
    actor: staff?.actor ?? "", reason: staff?.reason ?? "" }, challenge.accessToken, staff?.token);
}

export async function fetchReceipt(challenge: Challenge): Promise<Receipt> {
  if (USE_MOCK) return samples.get(challenge.id)!.receipt!;
  return req(`/api/challenges/${challenge.id}/receipt`, undefined, challenge.accessToken);
}
