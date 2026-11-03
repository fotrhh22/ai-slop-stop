begin;
create function pg_temp.waiting_fixture() returns uuid language plpgsql as $$
declare cid uuid:=gen_random_uuid(); aid uuid:=gen_random_uuid();
begin
 insert into public.challenges(id,target_image_path) values(cid,cid::text||'/target');
 insert into public.attempts(id,challenge_id,prompt,generation_status,router_verdict)
 values(aid,cid,'test','ready','PASS');
 insert into public.api_calls(challenge_id,attempt_id,operation,model,status,finished_at)
 values(cid,aid,'generate','test','succeeded',now());
 perform public.finalize_challenge(cid,aid,cid::text||'/'||aid::text,'router');
 return (select id from public.campaign_entries where challenge_id=cid);
end $$;
do $$
declare e uuid; cid uuid; sid uuid:=gen_random_uuid();
begin
 e:=pg_temp.waiting_fixture();select challenge_id into cid from public.campaign_entries where id=e;
 if public.delete_waiting_participant(e,'other-station') then raise exception 'cross station deletion'; end if;
 if not exists(select 1 from public.challenges where id=cid) then raise exception 'cross station lost data'; end if;
 if not public.delete_waiting_participant(e,'booth-1') then raise exception 'delete failed'; end if;
 if exists(select 1 from public.challenges where id=cid) or
    exists(select 1 from public.successes where challenge_id=cid) or
    exists(select 1 from public.api_calls where challenge_id=cid) or
    exists(select 1 from public.attempts where challenge_id=cid) or
    exists(select 1 from public.campaign_entries where id=e) then raise exception 'partial deletion'; end if;
 if (select count(*) from public.storage_cleanup_jobs where object_path like cid::text||'/%')<>2 then
  raise exception 'image cleanup not queued'; end if;
 if public.delete_waiting_participant(e,'booth-1') then raise exception 'duplicate delete changed state'; end if;
 e:=pg_temp.waiting_fixture();perform public.claim_campaign_entry(e,'booth-1',sid);
 begin
  perform public.delete_waiting_participant(e,'booth-1');raise exception 'claimed entry deleted';
 exception when raise_exception then if sqlerrm<>'CAMPAIGN_ENTRY_IN_USE' then raise; end if; end;
 if not exists(select 1 from public.campaign_entries where id=e) then raise exception 'measuring entry lost'; end if;
 e:=pg_temp.waiting_fixture();
 insert into public.print_jobs(receipt_id,challenge_id,station_id)
 select receipt_id,challenge_id,station_id from public.campaign_entries where id=e;
 begin
  perform public.delete_waiting_participant(e,'booth-1');raise exception 'print entry deleted';
 exception when raise_exception then if sqlerrm<>'CAMPAIGN_ENTRY_IN_USE' then raise; end if; end;
 if has_function_privilege('anon','public.delete_waiting_participant(uuid,text)','execute') then raise exception 'public delete access'; end if;
end $$;
rollback;
