# 미통과 세션 자동 삭제

기본 정책은 **마지막 생성·판정 관련 DB 변경 후 24시간**이 지난 미통과 세션을 정리하는 것이다.
브라우저 화면 삭제와 별도로 서버가 자동 처리한다. 단순 화면 조회는 보존 시간을 연장하지 않는다.

- 챌린지 세션, 프롬프트/시도, 생성·판정 호출 원장을 함께 삭제한다.
- 목표 이미지와 해당 세션의 미완료 업로드 이미지 경로는 DB 삭제 대기열에 넣고 Storage API로 제거한다.
  생성 실패 이미지는 원래 서버에 저장하지 않으므로 별도 생성 이미지 파일이 없는 경우도 있다.
- PASS 영수증이 있거나 세션이 succeeded이면 보존한다.
- Router PASS 판정 후 영수증 저장만 남아 있는 세션도 보존한다.
- 최근 진행 중 요청과 새 활동이 발생한 세션을 보호한다. 오래 멈춘 pending 호출은 영구 보존하지 않는다.
- DB 삭제와 이미지 삭제 대기열 생성은 한 트랜잭션이다. Storage 오류·삭제 응답 유실 시 같은 경로로 재시도한다.
- 삭제 API는 공개하지 않는다. 백엔드 service_role만 만료 처리를 실행할 수 있다.

## 적용

기존 마이그레이션 이후 아래 파일 전체를 Supabase SQL Editor에서 한 번 실행한다.

`supabase/migrations/202611040002_abandoned_session_cleanup.sql`

그 다음 새 서버 코드를 배포한다. 기존 Supabase URL/서버 키를 재사용하므로 새 비밀키는 필요 없다.
추가 Render 서비스나 유료 Cron 작업도 필요 없다.

기본 설정은 다음과 같으며 환경변수를 추가하지 않아도 이 값으로 실행된다.

```dotenv
SESSION_CLEANUP_ENABLED=true
UNPASSED_RETENTION_HOURS=24
CLEANUP_INTERVAL_SECONDS=900
```

서버 시작 시 한 번, 이후 15분마다 정리한다. 따라서 정상 가동 중에는 만료 후 다음 정리 주기에 삭제된다.
Render 서비스가 sleep/중지된 동안에는 실행되지 않고, **다음 기동 시 밀린 대상을 정리**한다.
이는 서버가 꺼져 있어도 정각에 실행되는 별도 클라우드 스케줄러는 아니다.
한 번에 세션 100개와 이미지 삭제 대기 200개를 처리하며 나머지는 다음 주기에 처리한다.

`UNPASSED_RETENTION_HOURS`는 1~8760시간 범위로 설정한다.
`SESSION_CLEANUP_ENABLED=false`로 정리를 비활성화할 수 있다. 설정 변경은 재배포 후 반영된다.

기존에 저장된 미통과 세션도 실제 마지막 활동을 기준으로 정리된다. PASS 기록은 그대로 남는다.
로그에는 삭제 개수/재시도 상태만 남기며 프롬프트·키·이미지는 기록하지 않는다.
브라우저 메모리의 이미지를 원격으로 지우거나, 다른 Pod에 남긴 개인 백업 파일을 삭제하는 기능은 아니다.

확인 쿼리:

```sql
select count(*) as remaining_storage_deletions from public.storage_cleanup_jobs;
select id,status,last_activity_at from public.challenges
where status<>'succeeded' order by last_activity_at limit 20;
```

이미지는 SQL로 Storage 메타데이터만 지우지 않고 [공식 Storage 삭제 API](https://supabase.com/docs/guides/storage/management/delete-objects)를 사용한다.
운영 SQL과 실제 데이터 삭제는 사용자가 적용하며, 구현 검증은 임시 로컬 DB에서만 수행했다.
