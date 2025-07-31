# Phase 1: Foundation - Async 지원 기반 구조

## 목표
MongoEngine에 async 지원을 위한 기반 구조를 구축합니다. 연결 관리와 기본적인 Document async 메서드를 구현합니다.

## 작업 항목

### 1. 하이브리드 연결 관리자 구현 ✅

#### 1.1 `connection.py` 수정
- [x] `connect_async()` 함수 추가
  - AsyncMongoClient 인스턴스 생성
  - 기존 _connections 딕셔너리에 저장
  - 연결 타입 메타데이터 추가
- [x] `is_async_connection()` 헬퍼 함수 추가
  - 현재 연결이 async인지 확인
  - alias 기반으로 판단
- [x] `disconnect_async()` 함수 추가
  - async 연결 정리
- [x] contextvars 기반 async 컨텍스트 관리
  - 기존 thread-local 대신 contextvars 사용
  - async 작업 간 연결 상태 유지

#### 1.2 연결 타입 감지 시스템
- [x] `_ConnectionType` enum 추가 (SYNC/ASYNC)
- [x] `_connection_types` 딕셔너리로 연결 타입 추적
- [x] `get_connection()` 수정하여 타입 체크 포함

### 2. Document 클래스에 async 메서드 추가 ✅

#### 2.1 `document.py` 수정
- [x] `async_save()` 메서드 구현
  - 연결 타입 확인
  - async insert/update 작업
  - 신호 지원 (일단 동기 신호 유지)
- [x] `async_delete()` 메서드 구현
  - async delete 작업
  - cascade 처리
- [x] `async_reload()` 메서드 구현
  - async find_one 작업
  - 필드 업데이트

#### 2.2 EmbeddedDocument 지원
- [x] `EmbeddedDocument.async_save()` 구현
  - 부모 문서의 async_save 호출

### 3. 기본 async 헬퍼 함수 ✅

#### 3.1 `async_utils.py` 생성
- [x] `async_get_db()` - async 데이터베이스 인스턴스 반환
- [x] `async_get_collection()` - async 컬렉션 인스턴스 반환
- [x] 에러 핸들링 유틸리티

### 4. 테스트 인프라 구축 ✅

#### 4.1 `tests/test_async_connection.py`
- [x] async 연결 테스트
- [x] 연결 타입 감지 테스트
- [x] 동기/비동기 혼용 시 에러 테스트

#### 4.2 `tests/test_async_document.py`
- [x] async_save 테스트
- [x] async_delete 테스트
- [x] async_reload 테스트

#### 4.3 pytest async 설정
- [x] `pytest-asyncio` 의존성 추가
- [x] async 테스트 fixtures 설정

### 5. 문서화 ✅

#### 5.1 docstring 추가
- [x] 모든 새 async 메서드에 상세 docstring
- [x] 사용 예시 포함

#### 5.2 README 업데이트
- [x] async 지원 언급
- [x] 간단한 사용 예시

## 구현 순서

1. 연결 관리자 (connection.py)
2. 기본 헬퍼 함수 (async_utils.py)
3. Document async 메서드
4. 테스트 작성
5. 문서화

## 예상 코드 구조

```python
# connection.py
async def connect_async(db=None, alias=DEFAULT_CONNECTION_NAME, **kwargs):
    """Async version of connect using AsyncMongoClient"""
    global _connections, _connection_types
    
    # AsyncMongoClient 생성
    connection = AsyncMongoClient(**kwargs)
    _connections[alias] = connection
    _connection_types[alias] = ConnectionType.ASYNC
    
    # 데이터베이스 설정
    if db:
        _dbs[alias] = connection[db]
    
    return connection

def is_async_connection(alias=DEFAULT_CONNECTION_NAME):
    """Check if the connection is async"""
    return _connection_types.get(alias) == ConnectionType.ASYNC

# document.py
class Document(BaseDocument):
    async def async_save(self, force_insert=False, validate=True, 
                        clean=True, write_concern=None, 
                        cascade=None, cascade_kwargs=None, 
                        _refs=None, save_condition=None, 
                        signal_kwargs=None):
        """Async version of save()"""
        if not is_async_connection(self._get_db_alias()):
            raise RuntimeError(
                "async_save() requires an async connection. "
                "Use connect_async() or use save() with sync connection."
            )
        
        # 실제 async 저장 로직
        # ...
```

## 완료 기준

- [x] 모든 테스트 통과
- [x] 기존 동기 코드 영향 없음 확인
- [x] async 연결로 기본 CRUD 작업 가능
- [x] 문서화 완료

## 주의사항

1. 기존 코드와의 완벽한 호환성 유지
2. 명확한 에러 메시지로 사용자 가이드
3. 성능 오버헤드 최소화
4. 코드 중복 최소화 (가능한 경우 공통 로직 추출)

## 완료 상태

**Phase 1 Foundation 구현 완료** (2025-07-31)

### 구현 내용:
- ✅ Async 연결 관리 시스템 구현 (connect_async, disconnect_async, is_async_connection)
- ✅ Document 클래스에 async 메서드 추가 (async_save, async_delete, async_reload, async_ensure_indexes, async_drop_collection)
- ✅ Async 헬퍼 유틸리티 구현 (async_utils.py)
- ✅ 포괄적인 테스트 스위트 작성 (connection, document, integration 테스트)
- ✅ README 문서 업데이트

### 테스트 결과:
- 총 23개 async 테스트 모두 통과
- 기존 동기 코드와의 완벽한 호환성 유지
- Sync/Async 연결 타입 체크 정상 작동

### 남은 작업:
- Cascade save for unsaved references (Phase 2에서 구현 예정)
- QuerySet async 지원 (Phase 2)
- 고급 async 기능들 (Phase 3-5)