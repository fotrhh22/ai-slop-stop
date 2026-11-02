-- Apply before IMAGE_PROVIDER=zeus. Existing Gemini rows remain NULL.
begin;
alter table public.api_calls add column if not exists energy_measurement jsonb
  check (energy_measurement is null or jsonb_typeof(energy_measurement) = 'object');
comment on column public.api_calls.energy_measurement is
  'v1 Zeus/NVML GPU-board generation-window telemetry. Joules canonical; includes GPU idle during window. NULL means unmeasured, never zero. Excludes Router, CPU, cooling and network. Active-challenge guard freezes this ledger at finalization.';
commit;
