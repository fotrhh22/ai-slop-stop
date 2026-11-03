-- Restore STAFF manual PASS while retaining Bike-end printing and fixed comparison targets.
begin;
create or replace function public.register_campaign_entry() returns trigger
language plpgsql security definer set search_path='' as $$
declare c public.challenges; basis text; target double precision;
 n integer; gemini_only boolean; gpu_only boolean; complete boolean; snapshot jsonb;
begin
 if new.pass_source not in ('router','staff') then raise exception 'INVALID_PASS_SOURCE'; end if;
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
notify pgrst,'reload schema';
commit;
