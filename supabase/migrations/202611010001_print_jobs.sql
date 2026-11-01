-- Apply manually in Supabase SQL Editor after the existing migrations.
begin;
alter table public.challenges add column if not exists print_station_id text;
comment on column public.challenges.print_station_id is '서버가 행사장 인증 후 생성 시 지정하는 출력 부스. NULL이면 자동 출력하지 않음.';
create table public.print_jobs (
 id uuid primary key default gen_random_uuid(),
 receipt_id uuid not null references public.successes(id),
 challenge_id uuid not null references public.challenges(id),
 station_id text not null,
 request_id uuid unique,
 status text not null default 'PENDING' check(status in ('PENDING','RENDERING','SENDING','SENT','FAILED','UNKNOWN')),
 lease_token uuid,
 lease_until timestamptz,
 cups_job_id text,
 error_code text,
 created_at timestamptz not null default now(),
 updated_at timestamptz not null default now()
);
create unique index print_jobs_initial_once on public.print_jobs(receipt_id,station_id) where request_id is null;
create index print_jobs_claim_idx on public.print_jobs(station_id,status,created_at);
alter table public.print_jobs enable row level security;
revoke all on public.print_jobs from anon, authenticated;
grant all on public.print_jobs to service_role;
comment on table public.print_jobs is 'PASS 저장과 같은 트랜잭션에서 생성하는 인쇄 큐. SENT는 CUPS 접수이며 실물 출력 완료를 보장하지 않음. UNKNOWN은 전송 결과 불확실, 자동 재전송 금지.';
create function public.enqueue_success_print() returns trigger
language plpgsql security definer set search_path='' as $$
declare station text;
begin
 select print_station_id into station from public.challenges where id=new.challenge_id;
 if station is not null then
  insert into public.print_jobs(receipt_id,challenge_id,station_id) values(new.id,new.challenge_id,station)
   on conflict do nothing;
 end if;
 return new;
end $$;
create trigger enqueue_success_print after insert on public.successes for each row execute function public.enqueue_success_print();

create function public.claim_print_job(p_station_id text) returns setof public.print_jobs
language plpgsql security definer set search_path='' as $$
declare picked uuid;
begin
 -- A sending job abandoned by a worker is uncertain, never automatically retried.
 update public.print_jobs set status='UNKNOWN', error_code='WORKER_INTERRUPTED',updated_at=now()
 where station_id=p_station_id and status='SENDING' and lease_until<now();
 select id into picked from public.print_jobs where station_id=p_station_id
 and (status='PENDING' or (status='RENDERING' and lease_until<now()))
 order by created_at,id limit 1 for update skip locked;
 if picked is null then return; end if;
 return query update public.print_jobs set status='RENDERING',lease_token=gen_random_uuid(),
 lease_until=now()+interval '5 minutes',updated_at=now(),error_code=null
 where id=picked returning *;
end $$;

create function public.advance_print_job(p_id uuid,p_station_id text,p_lease_token uuid,p_status text,
 p_cups_job_id text default null,p_error_code text default null) returns setof public.print_jobs
language plpgsql security definer set search_path='' as $$
begin
 return query update public.print_jobs set status=p_status,cups_job_id=coalesce(p_cups_job_id,cups_job_id),
 error_code=p_error_code,updated_at=now(),lease_until=now()+interval '5 minutes'
 where id=p_id and station_id=p_station_id and lease_token=p_lease_token and (
  (status='RENDERING' and lease_until>now() and p_status in ('SENDING','FAILED'))
  or (status='SENDING' and p_status in ('SENT','UNKNOWN'))
  or (status='UNKNOWN' and p_status='SENT' and p_cups_job_id is not null)
  or (status=p_status and p_status in ('SENDING','SENT','FAILED','UNKNOWN'))
 ) returning *;
end $$;
revoke all on function public.enqueue_success_print() from public,anon,authenticated;
revoke all on function public.claim_print_job(text) from public,anon,authenticated;
revoke all on function public.advance_print_job(uuid,text,uuid,text,text,text) from public,anon,authenticated;
grant execute on function public.claim_print_job(text) to service_role;
grant execute on function public.advance_print_job(uuid,text,uuid,text,text,text) to service_role;
commit;
