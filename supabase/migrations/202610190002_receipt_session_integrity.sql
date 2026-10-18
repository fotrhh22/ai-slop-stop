begin;
alter table public.challenges add column access_token_hash text;
alter table public.attempts add column generated_image_sha256 text;
alter table public.attempts add column evaluation_result jsonb;
comment on column public.challenges.access_token_hash is '익명 세션 접근 토큰의 SHA-256. 원문은 브라우저에만 반환. 기존 NULL 세션은 API 접근 불가.';
comment on column public.attempts.generated_image_sha256 is '서버가 실제 생성한 이미지 바이트의 SHA-256. 평가/성공 저장 이미지 위변조 검증.';
comment on column public.attempts.evaluation_result is '원본 Router 판정과 모델/유사도. PASS 저장 재시도에서 유료 평가를 반복하지 않기 위한 캐시.';
commit;
