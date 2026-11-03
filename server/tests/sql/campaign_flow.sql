-- Integration checks against a disposable local PostgreSQL, never production.
begin;
create function pg_temp.make_campaign(provider text, measured boolean default true) returns uuid
language plpgsql as $$
declare cid uuid:=gen_random_uuid(); aid uuid:=gen_random_uuid(); entry uuid;
begin
 insert into public.challenges(id,target_image_path) values(cid,'fixture');
 insert into public.attempts(id,challenge_id,prompt,generation_status,router_verdict,generated_image_sha256)
 values(aid,cid,'fixture','ready','PASS','fixture-hash');
 insert into public.api_calls(challenge_id,attempt_id,operation,model,provider,status,total_tokens,finished_at,energy_measurement)
 values(cid,aid,'generate','image-model',provider,'succeeded',111,now(),
 case when provider='zeus' and measured then '{"status":"measured","energy_j":360,"scope":"gpu_board_generation_window"}'::jsonb else null end),
 (cid,aid,'router','judge-model',provider,'succeeded',22,now(),
 case when provider='zeus' and measured then '{"status":"measured","energy_j":180,"scope":"gpu_board_router_window"}'::jsonb else null end);
 perform public.finalize_challenge(cid,aid,cid::text||'/'||aid::text,'router',null,null);
 select id into entry from public.campaign_entries where challenge_id=cid;
 return entry;
end $$;

do $$
declare e uuid; sid uuid:=gen_random_uuid(); row public.campaign_entries; r public.bike_results; n integer;
 started timestamptz:=now()-interval '20 seconds'; ended timestamptz:=now();
begin
 e:=pg_temp.make_campaign('gemini');
 select * into row from public.campaign_entries where id=e;
 assert row.energy_basis='tokens_only' and row.target_wh is null,'Gemini must not get invented Wh';
 assert not exists(select 1 from public.print_jobs where receipt_id=row.receipt_id),'AI PASS must not print';
 perform public.claim_campaign_entry(e,'booth-1',sid);
 begin
  perform public.claim_campaign_entry(e,'booth-1',gen_random_uuid());
  raise exception 'Expected competing claim rejection';
 exception when others then assert sqlerrm='CAMPAIGN_ENTRY_BUSY'; end;
 select * into r from public.record_bike_end(sid,e,'booth-1',0.0005,20,10,0,'usb','manual_stop',started,ended);
 assert r.target_wh is null and r.progress_pct is null and not r.target_reached;
 assert abs(r.campaign_energy_wh-0.05)<1e-10;
 perform public.record_bike_end(sid,e,'booth-1',0.0005,20,10,0,'usb','manual_stop',started,ended);
 select count(*) into n from public.print_jobs where bike_session_id=sid;
 assert n=1,'Retry must not print twice';
 begin
  perform public.record_bike_end(sid,e,'booth-1',0.0006,20,10,0,'usb','manual_stop',started,ended);
  raise exception 'Expected payload conflict';
 exception when others then assert sqlerrm='BIKE_RESULT_CONFLICT'; end;

 e:=pg_temp.make_campaign('zeus');sid:=gen_random_uuid();
 select * into row from public.campaign_entries where id=e;
 assert row.energy_basis='measured_gpu' and abs(row.target_wh-0.15)<1e-10;
 perform public.claim_campaign_entry(e,'booth-1',sid);
 begin
  perform public.record_bike_end(sid,e,'booth-1',0.0005,20,10,0,'usb','target_reached',started,ended);
  raise exception 'Expected false goal rejection';
 exception when others then assert sqlerrm='BIKE_RESULT_INVALID'; end;
 select * into r from public.record_bike_end(sid,e,'booth-1',0.0005,20,10,0,'usb','manual_stop',started,ended);
 assert not r.target_reached and abs(r.progress_pct-100.0/3)<1e-8,'Partial stop is a valid print trigger';
 assert exists(select 1 from public.print_jobs where bike_session_id=sid);

 e:=pg_temp.make_campaign('zeus');sid:=gen_random_uuid();
 perform public.claim_campaign_entry(e,'booth-1',sid);
 select * into r from public.record_bike_end(sid,e,'booth-1',0.0016,20,10,0,'ble','target_reached',started,ended);
 assert r.target_reached and r.progress_pct>100;

 e:=pg_temp.make_campaign('zeus',false);
 select * into row from public.campaign_entries where id=e;
 assert row.energy_basis='unavailable' and row.target_wh is null;
 begin
  perform public.claim_campaign_entry(e,'booth-1',gen_random_uuid());
  raise exception 'Expected unconfirmed energy rejection';
 exception when others then assert sqlerrm='ENERGY_UNCONFIRMED'; end;
 assert not has_table_privilege('anon','public.campaign_entries','SELECT');
 assert not has_function_privilege('anon','public.claim_campaign_entry(uuid,text,uuid)','EXECUTE');
end $$;
rollback;
