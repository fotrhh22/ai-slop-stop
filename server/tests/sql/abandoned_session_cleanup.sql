begin;
create function pg_temp.cleanup_fixture(age_hours integer,verdict text default 'FAIL',call_state text default 'succeeded')
returns uuid language plpgsql as $$
declare cid uuid:=gen_random_uuid(); aid uuid:=gen_random_uuid();
begin
 insert into public.challenges(id,target_image_path,created_at)
 values(cid,cid::text||'/target',now()-make_interval(hours=>age_hours));
 insert into public.attempts(id,challenge_id,prompt,generation_status,router_verdict)
 values(aid,cid,'fixture','ready',verdict);
 insert into public.api_calls(challenge_id,attempt_id,operation,model,status,started_at,finished_at)
 values(cid,aid,'generate','test',call_state,now()-make_interval(hours=>age_hours),
  case when call_state<>'pending' then now()-make_interval(hours=>age_hours) end);
 update public.challenges set last_activity_at=now()-make_interval(hours=>age_hours) where id=cid;
 return cid;
end $$;
do $$
declare expired uuid; recent uuid; pass_pending uuid; passed uuid; closed uuid; stale_call uuid; touched uuid; aid uuid; total integer;
begin
 expired:=pg_temp.cleanup_fixture(25);
 recent:=pg_temp.cleanup_fixture(23);
 pass_pending:=pg_temp.cleanup_fixture(100,'PASS');
 passed:=pg_temp.cleanup_fixture(100,'PASS');
 select id into aid from public.attempts where challenge_id=passed;
 perform public.finalize_challenge(passed,aid,passed::text||'/'||aid::text,'router');
 closed:=pg_temp.cleanup_fixture(30);update public.challenges set status='closed' where id=closed;
 stale_call:=pg_temp.cleanup_fixture(30,'FAIL','pending');
 touched:=pg_temp.cleanup_fixture(30);
 update public.attempts set prompt='new activity' where challenge_id=touched;
 if (select last_activity_at from public.challenges where id=touched)<now()-interval '1 minute' then
  raise exception 'activity not renewed'; end if;
 total:=public.expire_abandoned_challenges();
 if total<>3 then raise exception 'expected 3 deletions, got %',total; end if;
 if exists(select 1 from public.challenges where id in (expired,closed,stale_call)) or
    exists(select 1 from public.api_calls where challenge_id=expired) or
    exists(select 1 from public.attempts where challenge_id=expired) then raise exception 'not fully deleted'; end if;
 if (select count(*) from public.challenges where id in (recent,pass_pending,passed,touched))<>4 then
  raise exception 'recent/PASS record deleted'; end if;
 if not exists(select 1 from public.campaign_entries where challenge_id=passed) then raise exception 'PASS queue lost'; end if;
 if (select count(*) from public.storage_cleanup_jobs where object_path like expired::text||'/%')<>2 then
  raise exception 'durable storage deletion jobs missing'; end if;
 if exists(select 1 from public.storage_cleanup_jobs where object_path like passed::text||'/%') then
  raise exception 'PASS image queued for deletion'; end if;
 if public.expire_abandoned_challenges()<>0 then raise exception 'sweep not idempotent'; end if;
 if has_function_privilege('anon','public.expire_abandoned_challenges(integer,integer)','execute') or
    has_table_privilege('authenticated','public.storage_cleanup_jobs','select') then raise exception 'public cleanup access'; end if;
end $$;
rollback;
