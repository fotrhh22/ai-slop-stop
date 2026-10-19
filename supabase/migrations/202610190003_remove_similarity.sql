-- CLIP removal: Router compares images directly; no numeric similarity is stored.
begin;

alter table public.attempts
    drop column if exists similarity_raw,
    drop column if exists similarity_display;

commit;
