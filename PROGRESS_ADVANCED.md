# Phase 4: Advanced Features - Async Support Implementation

## Overview
This phase focuses on implementing advanced async features including signals, transactions, context managers, and aggregation framework support. We'll also implement the cascade operations that were deferred from Phase 3.

## Key Components to Implement

### 1. Async Cascade Operations (Deferred from Phase 3)

#### Cascade Delete Operations
```python
# mongoengine/document.py
async def _async_cascade_delete(self, *args, **kwargs):
    """Handle cascade deletes for references."""
    from mongoengine import CASCADE, NULLIFY, PULL, DENY
    
    delete_rules = self._meta.get("delete_rules") or {}
    
    for field_name, rule in delete_rules.items():
        field = self._fields[field_name]
        
        if rule == CASCADE:
            # Delete referenced documents
            if isinstance(field, ReferenceField):
                ref_doc = getattr(self, field_name)
                if ref_doc:
                    if isinstance(ref_doc, AsyncReferenceProxy):
                        ref_doc = await ref_doc.fetch()
                    if ref_doc:
                        await ref_doc.async_delete()
                        
        elif rule == NULLIFY:
            # Set references to this document to null
            ref_cls = field.document_type
            await ref_cls.objects.filter(**{field.reverse_delete_rule: self}).async_update(
                **{f"unset__{field.reverse_delete_rule}": 1}
            )
            
        elif rule == PULL:
            # Pull from ListFields
            ref_cls = field.document_type
            await ref_cls.objects.filter(**{f"{field.reverse_delete_rule}__in": [self]}).async_update(
                **{f"pull__{field.reverse_delete_rule}": self}
            )
            
        elif rule == DENY:
            # Check if references exist
            ref_cls = field.document_type
            if await ref_cls.objects.filter(**{field.reverse_delete_rule: self}).async_count() > 0:
                raise OperationError(f"Cannot delete - {ref_cls.__name__} documents still reference this")
```

### 2. Async Context Managers

#### async_switch_db
```python
# mongoengine/context_managers.py
class async_switch_db:
    """Async version of switch_db context manager."""
    
    def __init__(self, cls, db_alias):
        self.cls = cls
        self.collection = cls._get_collection()
        self.db_alias = db_alias
        self.ori_db_alias = cls._meta.get("db_alias", DEFAULT_CONNECTION_NAME)
    
    async def __aenter__(self):
        """Change the db_alias and clear the cached collection."""
        ensure_async_connection(self.db_alias)
        self.cls._meta["db_alias"] = self.db_alias
        self.cls._collection = None
        return self.cls
    
    async def __aexit__(self, t, value, traceback):
        """Reset the db_alias and collection."""
        self.cls._meta["db_alias"] = self.ori_db_alias
        self.cls._collection = self.collection
```

#### async_switch_collection
```python
class async_switch_collection:
    """Async version of switch_collection context manager."""
    
    def __init__(self, cls, collection_name):
        self.cls = cls
        self.ori_collection = cls._get_collection()
        self.ori_get_collection_name = cls._get_collection_name
        self.collection_name = collection_name
    
    async def __aenter__(self):
        """Change the collection name."""
        ensure_async_connection(self.cls._get_db_alias())
        
        @classmethod
        def _get_collection_name(cls):
            return self.collection_name
        
        self.cls._get_collection_name = _get_collection_name
        self.cls._collection = None
        return self.cls
    
    async def __aexit__(self, t, value, traceback):
        """Reset the collection."""
        self.cls._collection = self.ori_collection
        self.cls._get_collection_name = self.ori_get_collection_name
```

### 3. Async Transaction Support

#### async_run_in_transaction
```python
# mongoengine/context_managers.py
@asynccontextmanager
async def async_run_in_transaction(
    alias=DEFAULT_CONNECTION_NAME, 
    session_kwargs=None, 
    transaction_kwargs=None
):
    """Execute async queries within a database transaction.
    
    Usage:
        async with async_run_in_transaction():
            user = await User.objects.async_create(name="John")
            await user.async_update(email="john@example.com")
    """
    ensure_async_connection(alias)
    conn = get_connection(alias)
    
    session_kwargs = session_kwargs or {}
    async with await conn.start_session(**session_kwargs) as session:
        transaction_kwargs = transaction_kwargs or {}
        async with session.start_transaction(**transaction_kwargs):
            try:
                await _set_async_session(session)
                yield
                # Transaction auto-commits on successful exit
            except Exception:
                # Transaction auto-aborts on exception
                raise
            finally:
                await _set_async_session(None)
```

### 4. Aggregation Framework Support

#### async_aggregate
```python
# mongoengine/queryset/queryset.py
class BaseQuerySet:
    async def async_aggregate(self, pipeline, **kwargs):
        """Execute an aggregation pipeline asynchronously.
        
        :param pipeline: list of aggregation pipeline stages
        :param kwargs: additional options like allowDiskUse, maxTimeMS
        :return: CommandCursor with results
        """
        ensure_async_connection(self._document._get_db_alias())
        
        collection = self._document._get_collection()
        
        # Apply query filters as initial $match stage if needed
        if self._query:
            pipeline = [{"$match": self._query}] + list(pipeline)
        
        # Get current async session
        session = await _get_async_session()
        if session:
            kwargs['session'] = session
        
        # Execute aggregation
        cursor = collection.aggregate(pipeline, **kwargs)
        
        # Return async cursor for iteration
        return cursor
    
    async def async_distinct(self, field):
        """Get distinct values for a field asynchronously.
        
        :param field: the field to get distinct values for
        :return: list of distinct values
        """
        ensure_async_connection(self._document._get_db_alias())
        
        collection = self._document._get_collection()
        query = self._query
        
        session = await _get_async_session()
        
        return await collection.distinct(field, filter=query, session=session)
```

### 5. Field Projection Methods

#### async_values and async_values_list
```python
class BaseQuerySet:
    async def async_values(self, *fields):
        """Return dictionaries instead of Document instances.
        
        :param fields: fields to include in the result
        :return: list of dictionaries
        """
        ensure_async_connection(self._document._get_db_alias())
        
        # Set up projection
        self._fields_to_fetch = fields
        self.only(*fields)
        
        results = []
        async for doc in self:
            result_dict = {}
            for field in fields:
                value = getattr(doc, field, None)
                # Handle reference fields
                if isinstance(value, AsyncReferenceProxy):
                    value = value.pk  # Just get the ID
                result_dict[field] = value
            results.append(result_dict)
        
        return results
    
    async def async_values_list(self, *fields, flat=False):
        """Return tuples instead of Document instances.
        
        :param fields: fields to include in the result
        :param flat: if True and only one field, return flat list
        :return: list of tuples or values
        """
        ensure_async_connection(self._document._get_db_alias())
        
        results = []
        async for doc in self.only(*fields):
            if flat and len(fields) == 1:
                value = getattr(doc, fields[0], None)
                if isinstance(value, AsyncReferenceProxy):
                    value = value.pk
                results.append(value)
            else:
                values = []
                for field in fields:
                    value = getattr(doc, field, None)
                    if isinstance(value, AsyncReferenceProxy):
                        value = value.pk
                    values.append(value)
                results.append(tuple(values))
        
        return results
```

### 6. Query Optimization Methods

#### async_explain
```python
class BaseQuerySet:
    async def async_explain(self):
        """Get the query execution plan.
        
        :return: explanation of query execution
        """
        ensure_async_connection(self._document._get_db_alias())
        
        collection = self._document._get_collection()
        query = self._query
        
        session = await _get_async_session()
        
        # Build the find command with explain
        cursor = collection.find(query)
        if self._ordering:
            cursor = cursor.sort(self._ordering)
        if self._limit is not None:
            cursor = cursor.limit(self._limit)
        if self._skip is not None:
            cursor = cursor.skip(self._skip)
        
        return await cursor.explain(session=session)
```

### 7. Hybrid Signal System

#### Async Signal Support
```python
# mongoengine/signals.py
class AsyncSignal:
    """Signal that supports both sync and async handlers."""
    
    def __init__(self):
        self._sync_handlers = []
        self._async_handlers = []
    
    def connect(self, handler, sender=None, weak=True, dispatch_uid=None):
        """Connect a handler to the signal."""
        if asyncio.iscoroutinefunction(handler):
            self._async_handlers.append((handler, sender, weak, dispatch_uid))
        else:
            self._sync_handlers.append((handler, sender, weak, dispatch_uid))
    
    async def async_send(self, sender, **kwargs):
        """Send signal to all handlers asynchronously."""
        # Send to sync handlers
        for handler, registered_sender, weak, uid in self._sync_handlers:
            if registered_sender is None or registered_sender == sender:
                handler(sender=sender, **kwargs)
        
        # Send to async handlers
        tasks = []
        for handler, registered_sender, weak, uid in self._async_handlers:
            if registered_sender is None or registered_sender == sender:
                tasks.append(handler(sender=sender, **kwargs))
        
        if tasks:
            await asyncio.gather(*tasks)
    
    def send(self, sender, **kwargs):
        """Send signal synchronously (async handlers are skipped)."""
        for handler, registered_sender, weak, uid in self._sync_handlers:
            if registered_sender is None or registered_sender == sender:
                handler(sender=sender, **kwargs)

# Update existing signals to be hybrid
pre_save = AsyncSignal()
post_save = AsyncSignal()
pre_delete = AsyncSignal()
post_delete = AsyncSignal()
```

## Implementation Plan

### Step 1: Cascade Operations (Week 1)
- [x] Implement async cascade delete logic in Document
- [x] Add async support for CASCADE rule
- [x] Add async support for NULLIFY rule
- [x] Add async support for PULL rule
- [x] Add async support for DENY rule
- [x] Write comprehensive tests for cascade operations

### Step 2: Context Managers (Week 1)
- [x] Implement async_switch_db
- [x] Implement async_switch_collection
- [x] Implement async_no_dereference
- [x] Write tests for async context managers

### Step 3: Transaction Support (Week 1-2)
- [x] Implement async_run_in_transaction
- [x] Add async session management
- [x] Handle transaction commit/abort
- [x] Test nested transactions
- [x] Test cross-database transactions

### Step 4: Aggregation Framework (Week 2)
- [ ] Implement async_aggregate
- [ ] Implement async_distinct
- [ ] Support aggregation pipeline options
- [ ] Add cursor management for large results
- [ ] Write comprehensive aggregation tests

### Step 5: Field Projection (Week 2-3)
- [ ] Implement async_values
- [ ] Implement async_values_list
- [ ] Handle reference field projections
- [ ] Support nested field access
- [ ] Add performance tests

### Step 6: Query Optimization (Week 3)
- [ ] Implement async_explain
- [ ] Add async_hint support
- [ ] Implement index usage analysis
- [ ] Write optimization tests

### Step 7: Signal System (Week 3)
- [ ] Create AsyncSignal class
- [ ] Update existing signals to hybrid
- [ ] Support mixed sync/async handlers
- [ ] Test signal cascading
- [ ] Document signal usage patterns

### Step 8: Integration and Testing (Week 4)
- [ ] End-to-end async workflow tests
- [ ] Performance benchmarks
- [ ] Memory usage analysis
- [ ] Documentation updates
- [ ] Migration guide completion

## Usage Examples

### Cascade Operations
```python
class Author(Document):
    name = StringField()

class Book(Document):
    title = StringField()
    author = ReferenceField(Author, reverse_delete_rule=CASCADE)

# Deleting author cascades to books
author = await Author.objects.async_get(name="John")
await author.async_delete()  # All books by this author are also deleted
```

### Async Transactions
```python
async with async_run_in_transaction():
    user = User(name="John", balance=100)
    await user.async_save()
    
    # Transfer money
    await User.objects.filter(id=user.id).async_update(inc__balance=-50)
    await User.objects.filter(id=recipient.id).async_update(inc__balance=50)
    
    # If any operation fails, all are rolled back
```

### Aggregation Pipeline
```python
# Get author statistics
pipeline = [
    {"$group": {
        "_id": "$author",
        "book_count": {"$sum": 1},
        "avg_pages": {"$avg": "$pages"}
    }},
    {"$sort": {"book_count": -1}},
    {"$limit": 10}
]

async for result in await Book.objects.async_aggregate(pipeline):
    print(f"Author: {result['_id']}, Books: {result['book_count']}")
```

### Async Signals
```python
# Register async signal handler
@post_save.connect
async def notify_user_created(sender, document, **kwargs):
    if sender == User and kwargs.get('created'):
        await send_welcome_email(document.email)
        await update_statistics()

# Signal is triggered automatically
user = User(name="John", email="john@example.com")
await user.async_save()  # Triggers async signal handlers
```

## Testing Strategy

1. **Unit Tests**: Each async method tested in isolation
2. **Integration Tests**: Complex workflows with multiple async operations
3. **Transaction Tests**: ACID compliance and rollback scenarios
4. **Performance Tests**: Comparison with sync operations
5. **Concurrency Tests**: Multiple async operations in parallel
6. **Signal Tests**: Mixed sync/async handler execution

## Success Criteria

- [ ] All async methods work correctly with proper error handling
- [ ] No regression in sync functionality
- [ ] Transactions maintain ACID properties
- [ ] Signals work with both sync and async handlers
- [ ] Performance improvement in concurrent scenarios
- [ ] Comprehensive test coverage (>90%)
- [ ] Complete documentation with examples

## Notes

- Maintain backward compatibility - sync code must continue to work unchanged
- Use consistent `async_` prefix for all async methods
- Ensure proper cleanup in all error scenarios
- Consider connection pooling implications for transactions
- Handle edge cases gracefully with clear error messages
- Async signals should not block sync operations

## Dependencies

- Python 3.7+ (for asyncio improvements)
- PyMongo 4.0+ (for async support)
- pytest-asyncio for testing