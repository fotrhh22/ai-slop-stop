-- Apply after 202611030001_campaign_bike_flow.sql. No API keys are stored here.
begin;
create table public.gpu_runtimes (
 id uuid primary key,
 profile_id text not null check(profile_id in ('open-4090','open-5090')),
 station_id text not null,
 pod_id text not null check(pod_id ~ '^[a-zA-Z0-9]{5,40}$'),
 identity jsonb not null,
 created_at timestamptz not null default now(),
 unique(id,profile_id,station_id)
);
create table public.gpu_profile_bindings (
 profile_id text not null,
 station_id text not null,
 runtime_id uuid not null,
 primary key(profile_id,station_id),
 foreign key(runtime_id,profile_id,station_id) references public.gpu_runtimes(id,profile_id,station_id)
);
alter table public.gpu_runtimes enable row level security;
alter table public.gpu_profile_bindings enable row level security;
revoke all on public.gpu_runtimes,public.gpu_profile_bindings from anon,authenticated;
grant select,insert,update on public.gpu_runtimes,public.gpu_profile_bindings to service_role;

create function public.register_gpu_runtime(p_id uuid,p_profile_id text,p_station_id text,p_pod_id text,p_identity jsonb)
returns void language plpgsql security definer set search_path='' as $$
declare previous public.gpu_runtimes;
begin
 -- An existing runtime identity is immutable, including across re-registration.
 insert into public.gpu_runtimes(id,profile_id,station_id,pod_id,identity)
 values(p_id,p_profile_id,p_station_id,p_pod_id,p_identity) on conflict(id) do nothing;
 select * into previous from public.gpu_runtimes where id=p_id for update;
 if previous.profile_id<>p_profile_id or previous.station_id<>p_station_id or
    previous.pod_id<>p_pod_id or previous.identity<>p_identity then
   raise exception 'GPU_RUNTIME_CONFLICT';
 end if;
 insert into public.gpu_profile_bindings(profile_id,station_id,runtime_id)
 values(p_profile_id,p_station_id,p_id)
 on conflict(profile_id,station_id) do update set runtime_id=excluded.runtime_id;
end $$;
revoke all on function public.register_gpu_runtime(uuid,text,text,text,jsonb) from public,anon,authenticated;
grant execute on function public.register_gpu_runtime(uuid,text,text,text,jsonb) to service_role;
notify pgrst,'reload schema';
commit;
