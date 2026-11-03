begin;
create function pg_temp.reference_fixture() returns uuid language plpgsql as $$
declare cid uuid:=gen_random_uuid(); aid uuid:=gen_random_uuid();
begin
 insert into public.challenges(id,target_image_path) values(cid,cid::text||'/target');
 insert into public.attempts(id,challenge_id,prompt,generation_status,router_verdict) values(aid,cid,'reference test','ready','PASS');
 insert into public.api_calls(challenge_id,attempt_id,operation,model,status,finished_at)
 values(cid,aid,'generate','gemini-test','succeeded',now());
 perform public.finalize_challenge(cid,aid,cid::text||'/'||aid::text,'router');
 return (select id from public.campaign_entries where challenge_id=cid);
end $$;
do $$
declare e uuid; sid uuid:=gen_random_uuid(); row public.campaign_entries; result public.bike_results;
begin
 e:=pg_temp.reference_fixture();select * into row from public.campaign_entries where id=e;
 assert row.energy_basis='tokens_only' and row.target_wh is null,'No Gemini Wh estimate';
 assert row.reference_target_wh=0.3463 and row.reference_target_source like 'RTX4090_%','Expected benchmark reference';
 perform public.claim_campaign_entry(e,'booth-1',sid);
 select * into result from public.record_bike_end(sid,e,'booth-1',0.0035,30,30,0,'usb','target_reached',now()-interval '30 seconds',now());
 assert result.target_wh=0.3463 and result.target_reached and result.progress_pct>=100,'Fixed target and auto completion must agree';
 perform public.record_bike_end(sid,e,'booth-1',0.0035,30,30,0,'usb','target_reached',now()-interval '30 seconds',now());
 assert (select count(*) from public.print_jobs where bike_session_id=sid)=1,'Duplicate auto completion';
 e:=pg_temp.reference_fixture();sid:=gen_random_uuid();perform public.claim_campaign_entry(e,'booth-1',sid);
 begin
  perform public.record_bike_end(sid,e,'booth-1',0.0005,30,30,0,'usb','target_reached',now()-interval '30 seconds',now());
  raise exception 'False completion accepted';
 exception when raise_exception then if sqlerrm<>'BIKE_RESULT_INVALID' then raise; end if; end;
 select * into result from public.record_bike_end(sid,e,'booth-1',0.0005,30,30,0,'usb','manual_stop',now()-interval '30 seconds',now());
 assert result.target_wh=0.3463 and not result.target_reached and result.progress_pct>0,'Partial stop must retain reference';
end $$;
rollback;
