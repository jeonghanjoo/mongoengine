# Phase 2: QuerySet Async Support Implementation Progress

## 목표
MongoEngine의 QuerySet에 완전한 비동기 지원을 추가하여 효율적인 데이터베이스 쿼리를 가능하게 한다.

## 구현 범위

### 1. 기본 QuerySet 비동기 메서드
- [ ] `async_first()` - 첫 번째 결과 반환
- [ ] `async_get()` - 단일 문서 조회 (없으면 DoesNotExist, 여러 개면 MultipleObjectsReturned)
- [ ] `async_count()` - 쿼리 결과 개수 반환
- [ ] `async_to_list()` - 결과를 리스트로 변환
- [ ] `async_exists()` - 쿼리 결과 존재 여부 확인

### 2. 비동기 반복자
- [ ] `__aiter__()` - 비동기 반복 지원
- [ ] `__anext__()` - 다음 문서 반환
- [ ] 커서 관리 및 자동 정리
- [ ] 배치 처리 최적화

### 3. 벌크 작업
- [ ] `async_create()` - 새 문서 생성 및 저장
- [ ] `async_update()` - 여러 문서 업데이트
- [ ] `async_delete()` - 여러 문서 삭제
- [ ] `async_update_one()` - 단일 문서 업데이트
- [ ] `async_delete_one()` - 단일 문서 삭제

### 4. 고급 쿼리 기능
- [ ] `async_aggregate()` - 집계 파이프라인 실행
- [ ] `async_distinct()` - 고유 값 조회
- [ ] `async_explain()` - 쿼리 실행 계획
- [ ] `async_hint()` - 인덱스 힌트

### 5. 필드 작업
- [ ] `async_scalar()` - 단일 필드 값 반환
- [ ] `async_values()` - 딕셔너리 형태로 반환
- [ ] `async_values_list()` - 튜플 형태로 반환
- [ ] `async_only()` - 특정 필드만 조회
- [ ] `async_exclude()` - 특정 필드 제외

## 구현 세부 사항

### QuerySet 클래스 수정 전략

1. **기존 QuerySet 확장**
   - 동기 메서드는 그대로 유지
   - 비동기 메서드는 `async_` 접두사로 추가
   - 연결 타입 검증 로직 추가

2. **비동기 쿼리 실행**
   ```python
   async def async_first(self):
       """Get the first document matching the query."""
       if not is_async_connection(self._get_db_alias()):
           raise RuntimeError("Use first() with sync connection")
       
       # 쿼리 실행 로직
       cursor = await self._async_get_cursor()
       try:
           doc = await cursor.__anext__()
           return self._document._from_son(doc)
       except StopAsyncIteration:
           return None
   ```

3. **비동기 반복자 구현**
   ```python
   def __aiter__(self):
       """Async iterator support."""
       if not is_async_connection(self._get_db_alias()):
           raise RuntimeError("Use regular iteration with sync connection")
       return self
   
   async def __anext__(self):
       """Get next document asynchronously."""
       if not self._async_cursor:
           self._async_cursor = await self._async_get_cursor()
       
       try:
           doc = await self._async_cursor.__anext__()
           return self._document._from_son(doc)
       except StopAsyncIteration:
           await self._async_close_cursor()
           raise
   ```

### 주요 고려사항

1. **커서 관리**
   - 비동기 커서 생명주기 관리
   - 자동 정리 메커니즘
   - 메모리 누수 방지

2. **성능 최적화**
   - 배치 페칭 구현
   - 커서 재사용
   - 불필요한 쿼리 최소화

3. **에러 처리**
   - DoesNotExist, MultipleObjectsReturned 예외
   - 연결 타입 불일치 에러
   - 커서 관련 에러

4. **호환성**
   - 기존 QuerySet 체이닝 동작 유지
   - 동기/비동기 메서드 명확한 구분
   - 직관적인 API 설계

## 테스트 계획

### 단위 테스트
1. 각 비동기 메서드별 기본 동작 테스트
2. 에러 케이스 테스트
3. 연결 타입 검증 테스트

### 통합 테스트
1. 복잡한 쿼리 체이닝 테스트
2. 대용량 데이터 처리 테스트
3. 동시성 테스트

### 성능 테스트
1. 동기 vs 비동기 성능 비교
2. 메모리 사용량 측정
3. 동시 요청 처리 능력

## 구현 순서

1. **기본 구조 설정** (1일)
   - QuerySet 클래스에 비동기 지원 추가
   - 커서 관리 기반 구축

2. **핵심 메서드 구현** (3-4일)
   - async_first, async_get, async_count
   - 비동기 반복자

3. **벌크 작업** (2-3일)
   - async_create, async_update, async_delete

4. **고급 기능** (2-3일)
   - aggregate, distinct 등

5. **테스트 및 최적화** (2-3일)
   - 포괄적인 테스트 작성
   - 성능 최적화

## 참고 사항

- PyMongo의 AsyncCursor API 활용
- 기존 QuerySet 로직 최대한 재사용
- 명확한 문서화 필요