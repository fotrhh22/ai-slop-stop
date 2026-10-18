# Supabase 프로젝트 설정과 DB 설명

프로젝트 생성은 사용자가 진행합니다. 이 폴더는 스키마와 백엔드 연동 계약을 준비한 것이며, 현재 앱이 Supabase에 저장하도록 연결된 상태는 아닙니다.

## 1. 프로젝트 생성 후 실행

1. Supabase Dashboard에서 프로젝트 이름을 `ai-slop-stop`으로 설정합니다. 사용할 조직·요금제와 한국 사용자에게 가까운 리전을 선택하고 DB 비밀번호는 비밀번호 관리자에 보관합니다.
2. SQL Editor에서 `migrations/202610190001_initial_schema.sql` 전체를 한 번 실행합니다. 신규 프로젝트용 SQL이며 반복 실행용이 아닙니다.
3. Storage에서 다음 **비공개(private)** 버킷을 생성합니다. 두 버킷 모두 최대 파일 크기 10MB, 허용 MIME `image/png`, `image/jpeg`, `image/webp`를 설정합니다.

| 버킷 | 객체 경로 예 | 저장 시점 |
|---|---|---|
| `challenge-targets` | `{challenge_id}/target` | 새 세션 시작 시 목표 이미지 업로드 |
| `challenge-successes` | `{challenge_id}/{attempt_id}` | Router 또는 STAFF가 첫 PASS 확정 시 성공 이미지 업로드 |

객체에 확장자가 없어도 실제 MIME을 업로드 시 지정하면 됩니다. 파일은 Storage API로 업로드하고 `storage.objects`에 SQL로 직접 삽입하지 않습니다. DB에는 영구 객체 경로만 저장하고, 조회 시 백엔드에서 짧은 유효기간의 signed URL을 발급합니다.

4. 프로젝트 URL과 서버용 secret key 또는 legacy service-role key는 백엔드 환경변수로만 설정합니다. 다음은 **향후 연동할 설정 이름**이며 현재 서버는 아직 읽지 않습니다.

```dotenv
SUPABASE_URL=https://YOUR_PROJECT_REF.supabase.co
SUPABASE_SECRET_KEY=YOUR_SERVER_ONLY_KEY
```

실제 값을 GitHub나 프런트 `VITE_*` 환경변수에 넣지 않습니다. 로그인 없는 방문자도 FastAPI를 통해 이용하며 DB 직접 접근은 허용하지 않습니다. 세션 ID만으로 다른 사용자의 기록을 열 수 없도록 백엔드 연동 시 별도 세션 접근 토큰을 검증해야 합니다.

## 2. 테이블 역할

| 테이블 | 한 행의 의미 | 주요 컬럼 |
|---|---|---|
| `challenges` | 목표 이미지 하나에 대한 세션 | `id`, `target_image_path`, `status`, `created_at`, `completed_at` |
| `attempts` | 입력 프롬프트로 생성한 시도 하나 | `challenge_id`, `prompt`, `generation_status`, `router_verdict`, `router_reason`, 유사도 |
| `api_calls` | 공급자에게 실제 전송한 모델 요청 하나 | `attempt_id`, `operation`, `provider`, `model`, `status`, 토큰, `usage_metadata` |
| `successes` | 세션의 첫 PASS와 그 순간 누적값 | `challenge_id`, `attempt_id`, `prompt`, `image_path`, `pass_source`, 모델별 호출·토큰 |

관계: `challenges` 1:N `attempts`, `attempts` 1:N `api_calls`, `challenges` 1:0..1 `successes`.

성공 기록에는 세션 ID, 성공 이미지 경로, **프롬프트 원문**, `router/staff` 구분, 생성·Router·전체 호출 수, 모델별 토큰과 누락 수가 저장됩니다. SQL의 `COMMENT ON`으로 Dashboard에서도 테이블·핵심 컬럼 설명을 볼 수 있습니다.

FAIL 시에도 `attempts`와 `api_calls`는 기록합니다. 단, FAIL 이미지는 Storage에 저장하지 않습니다. 첫 PASS 후 해당 세션은 종료되므로 추가 시도는 새 세션에서 시작합니다. 이전 UI의 PASS 후 계속 생성 동작은 백엔드 연동 시 이에 맞춰 변경해야 합니다.

## 3. 호출 수와 토큰의 정확한 의미

- `generation_calls`: 실제 Gemini 이미지 생성 요청 수. SDK/래퍼가 재시도한 요청도 각각 포함합니다.
- `router_calls`: 실제 Router 요청 수. 같은 이미지를 다시 Try하면 추가됩니다.
- `model_calls_total`: 위 두 수의 합. CLIP 계산은 Gemini 호출이 아니므로 제외합니다.
- `failed_calls`: API 오류 수입니다. **Router가 정상 응답으로 FAIL을 판정한 경우는 호출 자체는 succeeded**이며 토큰은 그대로 누적됩니다.
- `known_tokens_total`: 응답에서 확인한 `total_tokens`의 합입니다. 토큰을 알 수 없는 오류·타임아웃은 `NULL`과 누락 횟수로 남깁니다. 알려지지 않은 사용량을 0으로 확정하지 않습니다.
- `tokens_by_model`: `gemini/실제모델ID` 키별 호출·알려진 토큰 합계와 누락 수입니다. 모델 ID는 설정값을 사용하고 표시용 이름을 하드코딩하지 않습니다.
- `thought_tokens`는 원본 의미대로 별도 저장합니다. 공급자의 total에 이미 포함될 수 있으므로 input/output/thought를 합쳐 total을 재구성하지 않습니다.

예: 생성 → Try FAIL → 생성 → Try FAIL → 생성 → Try PASS라면 `generation_calls=3`, `router_calls=3`, `model_calls_total=6`이며 세 번의 생성과 세 번의 판정 토큰이 모두 포함됩니다.

## 4. 백엔드 연동 순서

1. 서버가 UUID와 별도 접근 토큰을 발급하고 목표 이미지를 Storage에 업로드한 뒤 `challenges`를 생성합니다.
2. 프롬프트 원문으로 `attempts`를 만듭니다. 입력을 검증할 때 trim할 수 있지만 저장할 문자열 자체는 변경하지 않습니다.
3. **실제 공급자 요청 직전** `api_calls(pending)`를 삽입합니다. 응답 후 SDK usage 원본과 상태·완료 시각을 기록합니다. 재시도는 새 호출 행을 만듭니다. 로그 INSERT 재시도에는 동일한 UUID를 사용해 중복을 방지합니다.
4. Try 결과를 `attempts.router_verdict` 등에 저장합니다. STAFF는 이 원본 판정을 덮어쓰지 않습니다.
5. 첫 PASS 시 생성 이미지를 `challenge-successes/{challenge_id}/{attempt_id}`에 업로드합니다. 서버는 이미지가 실제 해당 시도의 결과인지 확인해야 하며, 브라우저가 제출한 이미지·PASS·합계를 그대로 신뢰하면 안 됩니다.
6. 백엔드에서 `finalize_challenge` RPC를 호출합니다. 이 함수가 세션 행을 잠그고 전체 원장을 집계해 성공 기록을 만든 뒤 세션을 종료합니다.
7. 성공 저장 완료를 확인한 후 화면에 완료 상태를 표시합니다. DB 오류라면 성공 저장을 재시도하며, UI만 완료 처리하지 않습니다.

예시 RPC 인자:

```json
{
  "p_challenge_id": "세션 UUID",
  "p_attempt_id": "성공 시도 UUID",
  "p_image_path": "세션 UUID/성공 시도 UUID",
  "p_pass_source": "router"
}
```

STAFF인 경우 `p_pass_source=staff`와 `p_staff_actor`, `p_staff_reason`을 전달합니다. **이 인자들은 인증이 아닙니다.** 서버가 운영자 권한을 먼저 검증해야 합니다. Router 경로는 attempts의 원본 판정이 PASS인 경우만 허용합니다.

## 5. 동시 요청·실패 처리

- 세션당 생성/평가 작업을 직렬화합니다. 호출 행이 `pending`이면 성공 확정 함수는 거절합니다. 서버 중단으로 남은 pending은 공급자 응답 확인 또는 `unknown` 전환 후 처리합니다.
- 원장 쓰기와 성공 확정은 같은 세션 잠금을 사용합니다. 확정 이후의 시도·호출 INSERT/UPDATE는 차단합니다.
- 세션당 성공은 UNIQUE 제약으로 한 번만 저장합니다. 함수 재호출은 기존 첫 성공을 반환합니다. 반환된 `attempt_id`를 기준으로 최종 성공을 표시합니다.
- Storage 업로드와 DB 트랜잭션은 원자적이지 않습니다. 업로드 후 DB 실패 시 같은 경로를 재사용해 RPC를 재시도합니다. 경쟁에서 선택되지 않은 업로드는 정리 대상으로 관리합니다.
- pending/unknown/usage 누락이 있으면 비용을 완전히 확인했다고 표시하지 않습니다.
- RLS를 켜고 anon/authenticated 접근 및 RPC 실행을 막습니다. 서버용 키는 RLS를 우회하므로 세션 소유권, STAFF 권한, 입력 검증은 FastAPI에서 수행해야 합니다.

## 6. 확인 SQL

```sql
select tablename, rowsecurity
from pg_tables
where schemaname = 'public'
  and tablename in ('challenges', 'attempts', 'api_calls', 'successes');

select id, public, file_size_limit, allowed_mime_types
from storage.buckets
where id in ('challenge-targets', 'challenge-successes');

select challenge_id, pass_source, generation_calls, router_calls,
       model_calls_total, known_tokens_total, calls_without_total_tokens
from public.successes
order by created_at desc;
```

## 참고 문서

검증용 `tests/schema_checks.sql`은 **개발용 DB**에서 마이그레이션 적용 후 실행할 수 있습니다. 테스트 데이터는 트랜잭션 종료 시 rollback합니다. 일반 PostgreSQL에서 실행할 경우 Supabase의 `anon`, `authenticated`, `service_role(BYPASSRLS)` 역할을 별도로 준비해야 합니다. Storage 설정은 이 테스트에 포함되지 않습니다.

- [Supabase migrations](https://supabase.com/docs/guides/deployment/database-migrations)
- [Storage quickstart](https://supabase.com/docs/guides/storage/quickstart)
- [Private buckets](https://supabase.com/docs/guides/storage/buckets/fundamentals)
- [Storage access control](https://supabase.com/docs/guides/storage/security/access-control)
