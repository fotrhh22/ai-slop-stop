-- Fixed comparison goal, NOT a Gemini energy estimate.
-- RTX 4090: FLUX.2 Klein 4B BF16 + Qwen3.5-4B INT8, mean of 3 runs.
-- See docs/runpod-session-operations.md (2026-11-03): 0.1590 + 0.1873 = 0.3463 Wh.
begin;
alter table public.campaign_entries add column reference_target_wh double precision
 check(reference_target_wh is null or (energy_basis='tokens_only' and reference_target_wh>0 and reference_target_wh<1000000));
alter table public.campaign_entries add column reference_target_source text;

create function public.set_gemini_reference_target() returns trigger
language plpgsql security definer set search_path='' as $$
begin
 if new.energy_basis='tokens_only' then
  new.reference_target_wh:=0.3463;
  new.reference_target_source:='RTX4090_FLUX2KLEIN4B_QWEN35INT8_3RUNS_20261103';
 end if;
 return new;
end $$;
create trigger gemini_reference_target before insert on public.campaign_entries
 for each row execute function public.set_gemini_reference_target();
-- Do not change an ongoing or completed session's existing target.
update public.campaign_entries set reference_target_wh=0.3463,
 reference_target_source='RTX4090_FLUX2KLEIN4B_QWEN35INT8_3RUNS_20261103'
 where energy_basis='tokens_only' and state='waiting' and bike_session_id is null;
revoke all on function public.set_gemini_reference_target() from public,anon,authenticated;

create or replace function public.record_bike_end(p_session_id uuid,p_entry_id uuid,p_station_id text,
 p_raw_wh double precision,p_elapsed_s double precision,p_measured_s double precision,p_gap_count integer,
 p_mode text,p_end_reason text,p_started_at timestamptz,p_ended_at timestamptz)
returns setof public.bike_results language plpgsql security definer set search_path='' as $$
declare entry public.campaign_entries; saved public.bike_results; amount double precision; pct double precision; reached boolean; effective_target double precision;
begin
 select * into entry from public.campaign_entries where id=p_entry_id and station_id=p_station_id for update;
 if not found then raise exception 'CAMPAIGN_ENTRY_NOT_FOUND'; end if;
 if entry.bike_session_id is distinct from p_session_id then raise exception 'BIKE_SESSION_MISMATCH'; end if;
 if p_raw_wh is null or not(p_raw_wh between 0 and 1000000)
  or p_elapsed_s is null or not(p_elapsed_s between 0 and 604800)
  or p_measured_s is null or not(p_measured_s between 0 and p_elapsed_s+0.001)
  or p_raw_wh > 6*p_measured_s/3600+0.000000001
  or p_gap_count is null or p_gap_count<0 or p_mode is null or p_mode not in ('usb','ble')
  or p_end_reason is null or p_end_reason not in ('target_reached','manual_stop','interrupted')
  or p_started_at is null or p_ended_at is null or p_ended_at<p_started_at then
  raise exception 'BIKE_RESULT_INVALID';
 end if;
 select * into saved from public.bike_results where id=p_session_id;
 if found then
  if saved.entry_id<>p_entry_id or saved.raw_energy_wh<>p_raw_wh or saved.elapsed_s<>p_elapsed_s
   or saved.measured_s<>p_measured_s or saved.gap_count<>p_gap_count or saved.mode<>p_mode
   or saved.end_reason<>p_end_reason or saved.started_at<>p_started_at or saved.ended_at<>p_ended_at then
   raise exception 'BIKE_RESULT_CONFLICT';
  end if;
  return next saved; return;
 end if;
 if entry.state<>'measuring' then raise exception 'BIKE_SESSION_MISMATCH'; end if;
 effective_target:=coalesce(entry.target_wh,entry.reference_target_wh);
 amount:=p_raw_wh*entry.multiplier;
 pct:=case when effective_target is null then null else amount/effective_target*100 end;
 reached:=effective_target is not null and amount>=effective_target;
 if p_end_reason='target_reached' and not reached then raise exception 'BIKE_RESULT_INVALID'; end if;
 insert into public.bike_results(id,entry_id,station_id,raw_energy_wh,campaign_energy_wh,multiplier,
  target_wh,progress_pct,target_reached,elapsed_s,measured_s,gap_count,mode,end_reason,started_at,ended_at)
 values(p_session_id,entry.id,p_station_id,p_raw_wh,amount,entry.multiplier,effective_target,pct,reached,
  p_elapsed_s,p_measured_s,p_gap_count,p_mode,p_end_reason,p_started_at,p_ended_at) returning * into saved;
 update public.campaign_entries set state='ended' where id=entry.id;
 insert into public.print_jobs(receipt_id,challenge_id,station_id,source,bike_session_id)
 values(entry.receipt_id,entry.challenge_id,p_station_id,'bike_session',p_session_id);
 return next saved;
end $$;

notify pgrst,'reload schema';
commit;
