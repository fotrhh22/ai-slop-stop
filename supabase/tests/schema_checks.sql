-- Run after the migration in a disposable PostgreSQL/Supabase database.
-- Fixtures are rolled back. ON_ERROR_STOP is recommended when using psql.
begin;
do $$
declare
  c uuid := gen_random_uuid();
  a uuid;
  winner uuid;
  pending_call uuid;
  first_result jsonb;
  repeated_result jsonb;
  i integer;
begin
  insert into public.challenges(id, target_image_path) values (c, c::text || '/target');
  for i in 1..3 loop
    a := gen_random_uuid();
    insert into public.attempts(id, challenge_id, prompt, generation_status, router_verdict)
    values (a, c, E'  original prompt\nline two  ', 'ready', case when i = 3 then 'PASS' else 'FAIL' end);
    insert into public.api_calls(challenge_id, attempt_id, operation, model, status,
      input_tokens, output_tokens, total_tokens, finished_at)
    values (c, a, 'generate', 'image-test', 'succeeded', 20, 80, 100, now()),
           (c, a, 'router', 'router-test', 'succeeded', 60, 40, 100, now());
    winner := a;
  end loop;
  -- A failed provider call with missing usage still counts.
  insert into public.api_calls(challenge_id, attempt_id, operation, model, status, finished_at)
    values (c, winner, 'generate', 'image-test', 'failed', now());
  insert into public.api_calls(challenge_id, attempt_id, operation, model)
    values (c, winner, 'router', 'router-test') returning id into pending_call;
  begin
    perform public.finalize_challenge(c, winner, c::text || '/' || winner::text, 'router');
    raise exception 'TEST_FAILED: pending accepted';
  exception when raise_exception then
    if SQLERRM <> 'MODEL_CALLS_STILL_PENDING' then raise; end if;
  end;
  -- Resolve an interrupted call without inventing usage.
  update public.api_calls set status = 'unknown', finished_at = now() where id = pending_call;
  first_result := public.finalize_challenge(c, winner, c::text || '/' || winner::text, 'router');
  if (first_result->>'model_calls_total')::int <> 8
     or (first_result->>'generation_calls')::int <> 4
     or (first_result->>'router_calls')::int <> 4
     or (first_result->>'known_tokens_total')::int <> 600
     or (first_result->>'calls_without_total_tokens')::int <> 2
     or (first_result->>'failed_calls')::int <> 1
     or first_result->>'prompt' <> E'  original prompt\nline two  '
     or (first_result#>>'{tokens_by_model,gemini/image-test,known_total_tokens}')::int <> 300 then
    raise exception 'TEST_FAILED: incorrect cumulative snapshot %', first_result;
  end if;
  repeated_result := public.finalize_challenge(c, winner, c::text || '/' || winner::text, 'router');
  if first_result <> repeated_result then raise exception 'TEST_FAILED: duplicate success'; end if;
  if (select status from public.challenges where id = c) <> 'succeeded' then
    raise exception 'TEST_FAILED: session not completed';
  end if;
  begin
    insert into public.api_calls(challenge_id, attempt_id, operation, model)
      values (c, winner, 'generate', 'image-test');
    raise exception 'TEST_FAILED: closed session accepted call';
  exception when raise_exception then
    if SQLERRM <> 'CHALLENGE_NOT_ACTIVE' then raise; end if;
  end;

  -- Router FAIL cannot finalize through the router path; STAFF keeps original FAIL.
  c := gen_random_uuid(); a := gen_random_uuid();
  insert into public.challenges(id, target_image_path) values (c, c::text || '/target');
  insert into public.attempts(id, challenge_id, prompt, generation_status, router_verdict)
    values (a, c, 'staff winning prompt', 'ready', 'FAIL');
  insert into public.api_calls(challenge_id, attempt_id, operation, model, status, total_tokens, finished_at)
    values (c, a, 'generate', 'image-test', 'succeeded', 100, now());
  begin
    perform public.finalize_challenge(c, a, c::text || '/' || a::text, 'router');
    raise exception 'TEST_FAILED: router FAIL accepted';
  exception when raise_exception then
    if SQLERRM <> 'ROUTER_HAS_NOT_PASSED' then raise; end if;
  end;
  begin
    perform public.finalize_challenge(c, a, c::text || '/' || a::text, 'staff');
    raise exception 'TEST_FAILED: unaudited STAFF accepted';
  exception when raise_exception then
    if SQLERRM <> 'STAFF_ACTOR_AND_REASON_REQUIRED' then raise; end if;
  end;
  first_result := public.finalize_challenge(c, a, c::text || '/' || a::text, 'staff', 'staff-test', 'manual review');
  if first_result->>'router_verdict' <> 'FAIL' or first_result->>'pass_source' <> 'staff' then
    raise exception 'TEST_FAILED: original verdict lost';
  end if;
  if exists (
    select 1 from pg_tables where schemaname = 'public'
      and tablename in ('challenges', 'attempts', 'api_calls', 'successes') and not rowsecurity
  ) then raise exception 'TEST_FAILED: RLS disabled'; end if;
  if has_table_privilege('anon', 'public.successes', 'SELECT')
     or has_table_privilege('authenticated', 'public.api_calls', 'INSERT')
     or has_function_privilege('anon', 'public.finalize_challenge(uuid,uuid,text,text,text,text)', 'EXECUTE') then
    raise exception 'TEST_FAILED: public access enabled';
  end if;
  raise notice 'PASS: FAIL usage, retries/errors, missing usage, exact prompt, pending guard, idempotency, closure, STAFF audit and public permissions';
end;
$$;
rollback;
