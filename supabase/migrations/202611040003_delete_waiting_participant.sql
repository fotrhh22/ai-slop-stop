-- Explicit operator deletion from the Bike waiting list. Apply after cleanup SQL.
begin;
create function public.delete_waiting_participant(p_entry_id uuid,p_station_id text)
returns boolean language plpgsql security definer set search_path='' as $$
declare entry public.campaign_entries; challenge public.challenges;
begin
 -- Same lock order as claiming/ending a bike session: first lock the entry.
 select * into entry from public.campaign_entries
  where id=p_entry_id and station_id=p_station_id for update;
 if not found then return false; end if;
 if entry.state<>'waiting' or entry.bike_session_id is not null or
    exists(select 1 from public.bike_results where entry_id=entry.id) or
    exists(select 1 from public.print_jobs where challenge_id=entry.challenge_id) then
  raise exception 'CAMPAIGN_ENTRY_IN_USE';
 end if;
 select * into challenge from public.challenges where id=entry.challenge_id for update;
 insert into public.storage_cleanup_jobs(bucket,object_path)
  values('challenge-targets',challenge.target_image_path) on conflict do nothing;
 insert into public.storage_cleanup_jobs(bucket,object_path)
  select 'challenge-successes',image_path from public.successes where challenge_id=entry.challenge_id
  on conflict do nothing;
 insert into public.storage_cleanup_jobs(bucket,object_path)
  select 'challenge-successes',entry.challenge_id::text||'/'||id::text from public.attempts
  where challenge_id=entry.challenge_id on conflict do nothing;
 delete from public.campaign_entries where id=entry.id;
 delete from public.successes where challenge_id=entry.challenge_id;
 delete from public.api_calls where challenge_id=entry.challenge_id;
 delete from public.attempts where challenge_id=entry.challenge_id;
 delete from public.challenges where id=entry.challenge_id;
 return true;
end $$;
revoke all on function public.delete_waiting_participant(uuid,text) from public,anon,authenticated;
grant execute on function public.delete_waiting_participant(uuid,text) to service_role;
notify pgrst,'reload schema';
commit;
