-- Additive tables only. Run in the existing Supabase SQL editor once.
begin;
create table if not exists public.pedal_event_state (
  id integer primary key check (id=1),
  revision bigint not null default 0,
  payload jsonb not null
);
insert into public.pedal_event_state(id,payload) values
 (1,'{"active":null,"sessions":{},"station":{},"commands":[],"gpu_pod_id":""}')
 on conflict (id) do nothing;
create table if not exists public.pedal_event_images (
  id uuid primary key,
  png_base64 text not null,
  created_at timestamptz not null default now()
);
alter table public.pedal_event_state enable row level security;
alter table public.pedal_event_images enable row level security;
revoke all on public.pedal_event_state,public.pedal_event_images from anon,authenticated;
grant all on public.pedal_event_state,public.pedal_event_images to service_role;
notify pgrst,'reload schema';
commit;
