-- Last write + 24h (default). PASS and PASS-save-pending sessions are preserved.
begin;
alter table public.challenges add column last_activity_at timestamptz not null default now();
update public.challenges c set last_activity_at=greatest(c.created_at,
 coalesce((select max(a.created_at) from public.attempts a where a.challenge_id=c.id),c.created_at),
 coalesce((select max(greatest(k.started_at,k.finished_at)) from public.api_calls k where k.challenge_id=c.id),c.created_at));
create index challenges_cleanup_activity on public.challenges(last_activity_at) where status<>'succeeded';

create function public.touch_challenge_activity() returns trigger
language plpgsql security definer set search_path='' as $$
begin
 update public.challenges set last_activity_at=clock_timestamp() where id=new.challenge_id;
 return new;
end $$;
create trigger attempts_touch_activity after insert or update on public.attempts
 for each row execute function public.touch_challenge_activity();
create trigger calls_touch_activity after insert or update on public.api_calls
 for each row execute function public.touch_challenge_activity();

-- Durable deletion outbox: deleting Storage SQL metadata does NOT remove bytes.
-- No prompts, participant details or API credentials are stored in this queue.
create table public.storage_cleanup_jobs (
 id uuid primary key default gen_random_uuid(),
 bucket text not null check(bucket in ('challenge-targets','challenge-successes')),
 object_path text not null,
 created_at timestamptz not null default now(),
 unique(bucket,object_path)
);
alter table public.storage_cleanup_jobs enable row level security;
revoke all on public.storage_cleanup_jobs from anon,authenticated;
grant select,delete on public.storage_cleanup_jobs to service_role;

create function public.expire_abandoned_challenges(p_retention_hours integer default 24,p_limit integer default 100)
returns integer language plpgsql security definer set search_path='' as $$
declare candidate public.challenges; removed integer:=0;
begin
 if p_retention_hours is null or p_retention_hours<1 or p_retention_hours>8760 or
    p_limit is null or p_limit<1 or p_limit>1000 then raise exception 'CLEANUP_POLICY_INVALID'; end if;
 for candidate in select c.* from public.challenges c
  where c.status<>'succeeded' and c.last_activity_at<now()-make_interval(hours=>p_retention_hours)
   and not exists(select 1 from public.successes s where s.challenge_id=c.id)
   and not exists(select 1 from public.attempts a where a.challenge_id=c.id and a.router_verdict='PASS')
   and not exists(select 1 from public.api_calls k where k.challenge_id=c.id and k.status='pending'
       and k.started_at>now()-interval '1 hour')
  order by c.last_activity_at,c.id limit p_limit for update of c skip locked
 loop
  insert into public.storage_cleanup_jobs(bucket,object_path)
   values('challenge-targets',candidate.target_image_path) on conflict do nothing;
  insert into public.storage_cleanup_jobs(bucket,object_path)
   select 'challenge-successes',candidate.id::text||'/'||a.id::text from public.attempts a
   where a.challenge_id=candidate.id on conflict do nothing;
  delete from public.api_calls where challenge_id=candidate.id;
  delete from public.attempts where challenge_id=candidate.id;
  delete from public.challenges where id=candidate.id;
  removed:=removed+1;
 end loop;
 return removed;
end $$;
revoke all on function public.touch_challenge_activity() from public,anon,authenticated;
revoke all on function public.expire_abandoned_challenges(integer,integer) from public,anon,authenticated;
grant execute on function public.expire_abandoned_challenges(integer,integer) to service_role;
notify pgrst,'reload schema';
commit;
