-- Disposable PostgreSQL only. Run after both campaign migrations.
begin;
do $$
declare first_id uuid:=gen_random_uuid(); second_id uuid:=gen_random_uuid();
begin
 perform public.register_gpu_runtime(first_id,'open-4090','test-station','oldpod1','{"gpu_uuid":"GPU-old"}');
 perform public.register_gpu_runtime(first_id,'open-4090','test-station','oldpod1','{"gpu_uuid":"GPU-old"}');
 if (select count(*) from public.gpu_runtimes where id=first_id)<>1 then raise exception 'duplicate runtime'; end if;
 begin
  perform public.register_gpu_runtime(first_id,'open-5090','test-station','otherpod','{"gpu_uuid":"GPU-other"}');
  raise exception 'should have conflicted';
 exception when raise_exception then
  if sqlerrm<>'GPU_RUNTIME_CONFLICT' then raise; end if;
 end;
 perform public.register_gpu_runtime(second_id,'open-4090','test-station','newpod1','{"gpu_uuid":"GPU-new"}');
 if (select runtime_id from public.gpu_profile_bindings where profile_id='open-4090' and station_id='test-station')<>second_id
   then raise exception 'binding not updated'; end if;
 if (select pod_id from public.gpu_runtimes where id=first_id)<>'oldpod1' then raise exception 'old session moved'; end if;
 if has_table_privilege('anon','public.gpu_runtimes','select') or
    has_function_privilege('authenticated','public.register_gpu_runtime(uuid,text,text,text,jsonb)','execute') then
  raise exception 'public runtime access';
 end if;
end $$;
rollback;
