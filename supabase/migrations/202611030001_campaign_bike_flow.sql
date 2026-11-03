-- Run manually AFTER 202611020001_gpu_energy.sql. Stop the print worker first.
begin;

alter table public.challenges add column if not exists campaign_station_id text not null default 'booth-1';
alter table public.challenges add column if not exists profile_snapshot jsonb not null default '{}'::jsonb;

create table public.campaign_entries (
 id uuid primary key default gen_random_uuid(),
 receipt_id uuid not null unique references public.successes(id),
 challenge_id uuid not null unique references public.challenges(id),
 participant_code text not null unique,
 station_id text not null,
 energy_basis text not null check(energy_basis in ('tokens_only','measured_gpu','unavailable')),
 target_wh double precision check(target_wh > 0 and target_wh < 1000000),
 multiplier integer not null default 100 check(multiplier=100),
 profile_snapshot jsonb not null,
 energy_snapshot jsonb not null,
 state text not null default 'waiting' check(state in ('waiting','measuring','ended')),
 bike_session_id uuid unique,
 created_at timestamptz not null default now(),
 check((energy_basis='measured_gpu' and target_wh is not null) or (energy_basis<>'measured_gpu' and target_wh is null))
);
create index campaign_entries_station on public.campaign_entries(station_id,state,created_at);

create table public.bike_results (
 id uuid primary key,
 entry_id uuid not null unique references public.campaign_entries(id),
 station_id text not null,
 raw_energy_wh double precision not null check(raw_energy_wh>=0 and raw_energy_wh<1000000),
 campaign_energy_wh double precision not null,
 multiplier integer not null check(multiplier=100),
 target_wh double precision,
 progress_pct double precision,
 target_reached boolean not null,
 elapsed_s double precision not null,
 measured_s double precision not null,
 gap_count integer not null,
 mode text not null check(mode in ('usb','ble')),
 end_reason text not null check(end_reason in ('target_reached','manual_stop','interrupted')),
 started_at timestamptz not null,
 ended_at timestamptz not null,
 created_at timestamptz not null default now()
);

alter table public.print_jobs add column if not exists source text not null default 'ai_pass_legacy';
alter table public.print_jobs add column if not exists bike_session_id uuid references public.bike_results(id);
create unique index print_jobs_bike_initial_once on public.print_jobs(bike_session_id,station_id) where request_id is null;

alter table public.campaign_entries enable row level security;
alter table public.bike_results enable row level security;
revoke all on public.campaign_entries,public.bike_results from anon,authenticated;
grant all on public.campaign_entries,public.bike_results to service_role;

-- Replace PASS -> print with PASS -> waiting entry. Old print rows are preserved,
-- but the updated claim function will not automatically print legacy rows.
drop trigger if exists enqueue_success_print on public.successes;
create function public.register_campaign_entry() returns trigger
language plpgsql security definer set search_path='' as $$
declare c public.challenges; basis text; target double precision;
 n integer; gemini_only boolean; gpu_only boolean; complete boolean; snapshot jsonb;
begin
 if new.pass_source <> 'router' then raise exception 'MANUAL_PASS_DISABLED'; end if;
 select * into c from public.challenges where id=new.challenge_id;
 select count(*),coalesce(bool_and(provider='gemini'),false),coalesce(bool_and(provider='zeus'),false),
  coalesce(bool_and(status='succeeded' or status='failed') and bool_and(coalesce(
   energy_measurement->>'status'='measured' and
   coalesce((energy_measurement->>'energy_j')::double precision,0)>0 and
   energy_measurement->>'scope'=case when operation='generate' then 'gpu_board_generation_window' else 'gpu_board_router_window' end
  ,false)),false),
  sum(case when energy_measurement->>'status'='measured' then (energy_measurement->>'energy_j')::double precision else 0 end)/3600,
  jsonb_agg(jsonb_build_object('id',id,'operation',operation,'provider',provider,'model',model,
   'total_tokens',total_tokens,'energy',energy_measurement) order by started_at,id)
 into n,gemini_only,gpu_only,complete,target,snapshot
 from public.api_calls where challenge_id=new.challenge_id;
 basis:=case when gemini_only then 'tokens_only' when gpu_only and complete and target>0 then 'measured_gpu' else 'unavailable' end;
 if basis<>'measured_gpu' then target:=null; end if;
 for attempt in 1..8 loop
  begin
   insert into public.campaign_entries(receipt_id,challenge_id,participant_code,station_id,
    energy_basis,target_wh,profile_snapshot,energy_snapshot)
   values(new.id,new.challenge_id,upper(substr(replace(gen_random_uuid()::text,'-',''),1,8)),
    c.campaign_station_id,basis,target,c.profile_snapshot,coalesce(snapshot,'[]'::jsonb));
   return new;
  exception when unique_violation then
   if exists(select 1 from public.campaign_entries where receipt_id=new.id) then return new; end if;
  end;
 end loop;
 raise exception 'PARTICIPANT_CODE_UNAVAILABLE';
end $$;
create trigger register_campaign_entry after insert on public.successes for each row execute function public.register_campaign_entry();

create function public.claim_campaign_entry(p_entry_id uuid,p_station_id text,p_session_id uuid)
returns setof public.campaign_entries language plpgsql security definer set search_path='' as $$
declare entry public.campaign_entries;
begin
 select * into entry from public.campaign_entries where id=p_entry_id and station_id=p_station_id for update;
 if not found then raise exception 'CAMPAIGN_ENTRY_NOT_FOUND'; end if;
 if entry.energy_basis='unavailable' then raise exception 'ENERGY_UNCONFIRMED'; end if;
 if entry.state='ended' then raise exception 'CAMPAIGN_ENTRY_ENDED'; end if;
 if entry.bike_session_id is not null and entry.bike_session_id<>p_session_id then raise exception 'CAMPAIGN_ENTRY_BUSY'; end if;
 return query update public.campaign_entries set state='measuring',bike_session_id=p_session_id where id=entry.id returning *;
end $$;

create function public.record_bike_end(p_session_id uuid,p_entry_id uuid,p_station_id text,
 p_raw_wh double precision,p_elapsed_s double precision,p_measured_s double precision,p_gap_count integer,
 p_mode text,p_end_reason text,p_started_at timestamptz,p_ended_at timestamptz)
returns setof public.bike_results language plpgsql security definer set search_path='' as $$
declare entry public.campaign_entries; saved public.bike_results; amount double precision; pct double precision; reached boolean;
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
 amount:=p_raw_wh*entry.multiplier;
 pct:=case when entry.target_wh is null then null else amount/entry.target_wh*100 end;
 reached:=entry.target_wh is not null and amount>=entry.target_wh;
 if p_end_reason='target_reached' and not reached then raise exception 'BIKE_RESULT_INVALID'; end if;
 insert into public.bike_results(id,entry_id,station_id,raw_energy_wh,campaign_energy_wh,multiplier,
  target_wh,progress_pct,target_reached,elapsed_s,measured_s,gap_count,mode,end_reason,started_at,ended_at)
 values(p_session_id,entry.id,p_station_id,p_raw_wh,amount,entry.multiplier,entry.target_wh,pct,reached,
  p_elapsed_s,p_measured_s,p_gap_count,p_mode,p_end_reason,p_started_at,p_ended_at) returning * into saved;
 update public.campaign_entries set state='ended' where id=entry.id;
 insert into public.print_jobs(receipt_id,challenge_id,station_id,source,bike_session_id)
 values(entry.receipt_id,entry.challenge_id,p_station_id,'bike_session',p_session_id);
 return next saved;
end $$;

create or replace function public.claim_print_job(p_station_id text) returns setof public.print_jobs
language plpgsql security definer set search_path='' as $$
declare picked uuid;
begin
 update public.print_jobs set status='UNKNOWN',error_code='WORKER_INTERRUPTED',updated_at=now()
 where station_id=p_station_id and status='SENDING' and lease_until<now();
 select id into picked from public.print_jobs where station_id=p_station_id and source='bike_session'
 and (status='PENDING' or (status='RENDERING' and lease_until<now()))
 order by created_at,id limit 1 for update skip locked;
 if picked is null then return; end if;
 return query update public.print_jobs set status='RENDERING',lease_token=gen_random_uuid(),
 lease_until=now()+interval '5 minutes',updated_at=now(),error_code=null where id=picked returning *;
end $$;

revoke all on function public.register_campaign_entry() from public,anon,authenticated;
revoke all on function public.claim_campaign_entry(uuid,text,uuid) from public,anon,authenticated;
revoke all on function public.record_bike_end(uuid,uuid,text,double precision,double precision,double precision,integer,text,text,timestamptz,timestamptz) from public,anon,authenticated;
grant execute on function public.claim_campaign_entry(uuid,text,uuid) to service_role;
grant execute on function public.record_bike_end(uuid,uuid,text,double precision,double precision,double precision,integer,text,text,timestamptz,timestamptz) to service_role;
notify pgrst,'reload schema';
commit;
