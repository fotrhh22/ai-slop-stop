-- New Supabase project: run once in SQL Editor.
-- Storage buckets are created separately through Dashboard/Storage API.
begin;

create table public.challenges (
  id uuid primary key default gen_random_uuid(),
  title text not null default '새 챌린지',
  target_image_path text not null check (length(target_image_path) > 0),
  status text not null default 'active' check (status in ('active', 'succeeded', 'closed')),
  created_at timestamptz not null default now(),
  completed_at timestamptz,
  check ((status = 'succeeded') = (completed_at is not null))
);

create table public.attempts (
  id uuid primary key default gen_random_uuid(),
  challenge_id uuid not null references public.challenges(id),
  prompt text not null check (length(btrim(prompt)) > 0),
  generation_status text not null default 'pending'
    check (generation_status in ('pending', 'ready', 'failed')),
  router_verdict text check (router_verdict in ('PASS', 'FAIL')),
  router_reason text,
  similarity_raw double precision check (similarity_raw between -1 and 1),
  similarity_display double precision check (similarity_display between 0 and 1),
  created_at timestamptz not null default now(),
  unique (id, challenge_id)
);

create table public.api_calls (
  id uuid primary key default gen_random_uuid(),
  challenge_id uuid not null references public.challenges(id),
  attempt_id uuid not null,
  operation text not null check (operation in ('generate', 'router')),
  provider text not null default 'gemini',
  model text not null check (length(btrim(model)) > 0),
  status text not null default 'pending'
    check (status in ('pending', 'succeeded', 'failed', 'unknown')),
  input_tokens bigint check (input_tokens >= 0),
  output_tokens bigint check (output_tokens >= 0),
  thought_tokens bigint check (thought_tokens >= 0),
  total_tokens bigint check (total_tokens >= 0),
  usage_metadata jsonb check (jsonb_typeof(usage_metadata) = 'object'),
  error_code text,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  foreign key (attempt_id, challenge_id) references public.attempts(id, challenge_id),
  check ((status = 'pending') = (finished_at is null))
);

create table public.successes (
  id uuid primary key default gen_random_uuid(),
  challenge_id uuid not null unique references public.challenges(id),
  attempt_id uuid not null,
  prompt text not null,
  image_path text not null check (length(image_path) > 0),
  pass_source text not null check (pass_source in ('router', 'staff')),
  staff_actor text,
  staff_reason text,
  router_verdict text check (router_verdict in ('PASS', 'FAIL')),
  router_reason text,
  model_calls_total bigint not null check (model_calls_total >= 0),
  generation_calls bigint not null check (generation_calls >= 0),
  router_calls bigint not null check (router_calls >= 0),
  failed_calls bigint not null check (failed_calls >= 0),
  tokens_by_model jsonb not null check (jsonb_typeof(tokens_by_model) = 'object'),
  known_tokens_total bigint not null check (known_tokens_total >= 0),
  calls_without_total_tokens bigint not null check (calls_without_total_tokens >= 0),
  created_at timestamptz not null default now(),
  foreign key (attempt_id, challenge_id) references public.attempts(id, challenge_id),
  check (model_calls_total = generation_calls + router_calls),
  check (pass_source <> 'staff' or (
    length(btrim(coalesce(staff_actor, ''))) > 0 and
    length(btrim(coalesce(staff_reason, ''))) > 0
  )),
  check (pass_source <> 'router' or (router_verdict is not distinct from 'PASS'))
);

create index attempts_challenge_created_idx on public.attempts(challenge_id, created_at);
create index api_calls_challenge_started_idx on public.api_calls(challenge_id, started_at);
create index api_calls_attempt_idx on public.api_calls(attempt_id);
create index successes_created_idx on public.successes(created_at);

-- Serialize writes with success finalization; closed sessions cannot accrue new calls.
create function public.guard_active_challenge()
returns trigger language plpgsql set search_path = '' as $$
declare
  v_status text;
begin
  if TG_OP = 'UPDATE' and (
    new.challenge_id is distinct from old.challenge_id or new.id is distinct from old.id
  ) then
    raise exception 'RECORD_IDENTITY_IMMUTABLE';
  end if;
  select status into v_status from public.challenges where id = new.challenge_id for update;
  if not found or v_status <> 'active' then
    raise exception 'CHALLENGE_NOT_ACTIVE';
  end if;
  return new;
end;
$$;

create trigger attempts_active_challenge before insert or update on public.attempts
  for each row execute function public.guard_active_challenge();
create trigger api_calls_active_challenge before insert or update on public.api_calls
  for each row execute function public.guard_active_challenge();

-- Backend-only RPC. Upload the winning image before calling this function.
-- A retry after a successful transaction returns the existing first success.
create function public.finalize_challenge(
  p_challenge_id uuid,
  p_attempt_id uuid,
  p_image_path text,
  p_pass_source text,
  p_staff_actor text default null,
  p_staff_reason text default null
)
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare
  v_challenge public.challenges%rowtype;
  v_attempt public.attempts%rowtype;
  v_success public.successes%rowtype;
  v_by_model jsonb;
begin
  select * into v_challenge from public.challenges where id = p_challenge_id for update;
  if not found then raise exception 'CHALLENGE_NOT_FOUND'; end if;

  select * into v_success from public.successes where challenge_id = p_challenge_id;
  if found then return to_jsonb(v_success); end if;
  if v_challenge.status <> 'active' then raise exception 'CHALLENGE_NOT_ACTIVE'; end if;
  if p_pass_source is null or p_pass_source not in ('router', 'staff') then
    raise exception 'INVALID_PASS_SOURCE';
  end if;
  if p_image_path is null or p_image_path <> p_challenge_id::text || '/' || p_attempt_id::text then
    raise exception 'INVALID_IMAGE_PATH';
  end if;

  select * into v_attempt from public.attempts
    where id = p_attempt_id and challenge_id = p_challenge_id;
  if not found or v_attempt.generation_status <> 'ready' then
    raise exception 'ATTEMPT_NOT_READY';
  end if;
  if p_pass_source = 'router' and v_attempt.router_verdict is distinct from 'PASS' then
    raise exception 'ROUTER_HAS_NOT_PASSED';
  end if;
  if p_pass_source = 'staff' and (
    length(btrim(coalesce(p_staff_actor, ''))) = 0 or
    length(btrim(coalesce(p_staff_reason, ''))) = 0
  ) then raise exception 'STAFF_ACTOR_AND_REASON_REQUIRED'; end if;
  if exists (select 1 from public.api_calls where challenge_id = p_challenge_id and status = 'pending') then
    raise exception 'MODEL_CALLS_STILL_PENDING';
  end if;
  if not exists (
    select 1 from public.api_calls where attempt_id = p_attempt_id
      and operation = 'generate' and status = 'succeeded'
  ) then raise exception 'SUCCESSFUL_GENERATION_REQUIRED'; end if;

  select coalesce(jsonb_object_agg(provider || '/' || model, usage), '{}'::jsonb)
    into v_by_model
  from (
    select provider, model, jsonb_build_object(
      'calls', count(*),
      'generation_calls', count(*) filter (where operation = 'generate'),
      'router_calls', count(*) filter (where operation = 'router'),
      'failed_calls', count(*) filter (where status = 'failed'),
      'unknown_calls', count(*) filter (where status = 'unknown'),
      'known_input_tokens', coalesce(sum(input_tokens), 0),
      'known_output_tokens', coalesce(sum(output_tokens), 0),
      'known_thought_tokens', coalesce(sum(thought_tokens), 0),
      'known_total_tokens', coalesce(sum(total_tokens), 0),
      'calls_without_input_tokens', count(*) filter (where input_tokens is null),
      'calls_without_output_tokens', count(*) filter (where output_tokens is null),
      'calls_without_thought_tokens', count(*) filter (where thought_tokens is null),
      'calls_without_total_tokens', count(*) filter (where total_tokens is null)
    ) as usage
    from public.api_calls where challenge_id = p_challenge_id
    group by provider, model
  ) grouped;

  insert into public.successes (
    challenge_id, attempt_id, prompt, image_path, pass_source, staff_actor, staff_reason,
    router_verdict, router_reason, model_calls_total, generation_calls, router_calls,
    failed_calls, tokens_by_model, known_tokens_total, calls_without_total_tokens
  )
  select p_challenge_id, p_attempt_id, v_attempt.prompt, p_image_path, p_pass_source,
    case when p_pass_source = 'staff' then p_staff_actor end,
    case when p_pass_source = 'staff' then p_staff_reason end,
    v_attempt.router_verdict, v_attempt.router_reason,
    count(*), count(*) filter (where operation = 'generate'),
    count(*) filter (where operation = 'router'), count(*) filter (where status = 'failed'),
    v_by_model, coalesce(sum(total_tokens), 0), count(*) filter (where total_tokens is null)
  from public.api_calls where challenge_id = p_challenge_id
  returning * into v_success;

  update public.challenges set status = 'succeeded', completed_at = now() where id = p_challenge_id;
  return to_jsonb(v_success);
end;
$$;

-- Public visitors use FastAPI, never the database directly.
alter table public.challenges enable row level security;
alter table public.attempts enable row level security;
alter table public.api_calls enable row level security;
alter table public.successes enable row level security;
revoke all on public.challenges, public.attempts, public.api_calls, public.successes from anon, authenticated;
grant select, insert, update on public.challenges, public.attempts, public.api_calls to service_role;
grant select, insert on public.successes to service_role;
revoke all on function public.guard_active_challenge() from public, anon, authenticated;
revoke all on function public.finalize_challenge(uuid, uuid, text, text, text, text) from public, anon, authenticated;
grant execute on function public.finalize_challenge(uuid, uuid, text, text, text, text) to service_role;

comment on table public.challenges is '목표 이미지별 세션. 백엔드 발급 UUID를 사용하고 첫 PASS 시 종료한다.';
comment on column public.challenges.target_image_path is 'challenge-targets 비공개 버킷의 객체 경로. 만료되는 signed URL을 저장하지 않는다.';
comment on table public.attempts is '프롬프트 원문과 생성·Router 결과. FAIL도 유지하며 실패 이미지 자체는 저장하지 않는다.';
comment on column public.attempts.prompt is '사용자가 제출한 프롬프트 원문. 성공 시 successes에 그대로 복사한다.';
comment on table public.api_calls is '실제 모델 요청 1회당 1행. FAIL 판정, API 오류, 재시도를 모두 누적한다.';
comment on column public.api_calls.status is 'pending=진행중, succeeded=정상 응답(판정 FAIL도 포함), failed=호출 오류, unknown=요청 결과 확인 불가.';
comment on column public.api_calls.total_tokens is '공급자 응답의 총 토큰. 알 수 없으면 NULL. input/output/thought 합으로 임의 재계산하지 않는다.';
comment on column public.api_calls.usage_metadata is '공급자가 반환한 usage 원본 JSON. 이미지·추론 토큰 등 세부 항목 보존.';
comment on table public.successes is '세션의 첫 PASS 1건. 성공 이미지 경로, 프롬프트 원문, 성공까지 FAIL 포함 사용량 스냅샷.';
comment on column public.successes.image_path is 'challenge-successes 비공개 버킷 객체 경로: {challenge_id}/{attempt_id}. MIME은 업로드 시 지정.';
comment on column public.successes.generation_calls is '성공까지 실제 이미지 생성 모델 호출 수. 자동 재시도와 호출 오류 포함. Try 횟수와 구별.';
comment on column public.successes.model_calls_total is '성공까지 생성+Router 실제 호출 수. CLIP은 로컬 계산이므로 제외.';
comment on column public.successes.tokens_by_model is 'provider/model별 알려진 토큰 합계와 누락 수. FAIL 및 오류 요청도 집계.';
comment on column public.successes.known_tokens_total is '공급자 total_tokens가 알려진 호출만 합산. 누락이 있으면 실제 전체 토큰과 다를 수 있다.';
comment on column public.successes.calls_without_total_tokens is '총 토큰을 확인할 수 없는 호출 수. 0이 아니면 집계 불완전.';
comment on function public.finalize_challenge(uuid, uuid, text, text, text, text) is '백엔드 전용 첫 PASS 확정. 원문·누적 사용량 저장과 세션 종료를 한 트랜잭션으로 실행. STAFF 인증과 Storage 업로드는 호출 전에 백엔드에서 처리.';

commit;
