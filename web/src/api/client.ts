// 백엔드 API 클라이언트.
// 브라우저는 얇은 백엔드(/api)만 호출한다. Gemini API 키는 백엔드에만 둔다.
//
// 1단계(현재): USE_MOCK=true 이면 백엔드 없이 가짜 응답으로 UI 흐름을 돌린다.
// 이후 단계에서 USE_MOCK=false 로 바꾸면 실제 /api/generate, /api/evaluate 를 호출.

import type { Verdict } from "../types";

// Vite 환경변수. .env.local 에 VITE_USE_MOCK=false 로 두면 실제 백엔드 호출.
const USE_MOCK = (import.meta.env.VITE_USE_MOCK ?? "true") !== "false";

export interface GenerateResult {
  imageUrl: string;
}

export interface EvaluateResult {
  similarity: number; // 표시용 재스케일 점수 (0~1). 일치율 %가 아님.
  similarity_raw?: number; // 원시 CLIP 코사인
  verdict: Verdict; // Router 최종 판정 PASS/FAIL
  reason: string; // 짧은 판정 이유
}

async function req<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let code = `HTTP_${res.status}`;
    try {
      const b = await res.json();
      code = b?.detail ?? b?.error_code ?? code;
    } catch {
      /* ignore */
    }
    throw new Error(code);
  }
  return res.json();
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// 샘플 생성 이미지(placeholder). 실제 Gemini 연결 전까지 사용.
function mockImage(prompt: string): string {
  const seed = encodeURIComponent(prompt.slice(0, 24) || "image");
  // picsum 의 seed 엔드포인트로 프롬프트마다 다른 샘플 이미지
  return `https://picsum.photos/seed/${seed}/512/512`;
}

export async function generateImage(prompt: string): Promise<GenerateResult> {
  if (USE_MOCK) {
    await sleep(900);
    return { imageUrl: mockImage(prompt) };
  }
  return req<GenerateResult>("/api/generate", { prompt });
}

export async function evaluate(
  targetImageUrl: string,
  generatedImageUrl: string
): Promise<EvaluateResult> {
  if (USE_MOCK) {
    await sleep(900);
    const similarity = Math.round((0.6 + Math.random() * 0.39) * 100) / 100;
    const verdict: Verdict = similarity >= 0.85 ? "PASS" : "FAIL";
    const reason =
      verdict === "PASS"
        ? "주요 피사체·구도·배경이 목표 이미지와 충분히 일치합니다."
        : "주요 피사체는 유사하지만 배경과 구도가 다릅니다.";
    return { similarity, similarity_raw: similarity, verdict, reason };
  }
  return req<EvaluateResult>("/api/evaluate", {
    target_image: targetImageUrl,
    generated_image: generatedImageUrl,
  });
}
