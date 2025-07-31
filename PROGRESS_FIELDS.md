# Phase 3: Fields and References - Async Support Implementation

## Overview
This phase focuses on adding async support to MongoEngine's field system, particularly for reference fields that require database lookups and GridFS operations for file storage.

## Key Components to Implement

### 1. ReferenceField Async Support

#### Current Behavior
- ReferenceField uses descriptor protocol (`__get__`) for lazy loading
- Automatically dereferences DBRefs when accessed
- Synchronous database lookup blocks the event loop

#### Async Implementation Strategy
```python
class ReferenceField(BaseField):
    # Keep existing __get__ for sync contexts
    def __get__(self, instance, owner):
        if is_async_connection(instance._get_db_alias()):
            # Return a proxy object that requires explicit async dereferencing
            return AsyncReferenceProxy(self, instance)
        # Existing sync logic...
    
    # New async method for explicit dereferencing
    async def async_fetch(self, instance):
        """Async version of reference dereferencing."""
        ref_value = instance._data.get(self.name)
        if isinstance(ref_value, DBRef):
            cls = self._get_ref_document_class(ref_value)
            dereferenced = await self._async_lazy_load_ref(cls, ref_value, instance)
            instance._data[self.name] = dereferenced
            return dereferenced
        return ref_value
    
    @staticmethod
    async def _async_lazy_load_ref(ref_cls, dbref, instance):
        """Async version of _lazy_load_ref."""
        db = ref_cls._get_db()
        collection = db[dbref.collection]
        
        # Get current async session if any
        session = await _get_async_session()
        
        # Use async find_one
        dereferenced_doc = await collection.find_one(
            {"_id": dbref.id},
            session=session
        )
        
        if dereferenced_doc is None:
            raise DoesNotExist(f"Trying to dereference unknown document {dbref}")
        
        return ref_cls._from_son(dereferenced_doc)
```

#### AsyncReferenceProxy Design
```python
class AsyncReferenceProxy:
    """Proxy object for async reference field access."""
    
    def __init__(self, field, instance):
        self.field = field
        self.instance = instance
        self._cached_value = None
    
    async def fetch(self):
        """Explicitly fetch the referenced document."""
        if self._cached_value is None:
            self._cached_value = await self.field.async_fetch(self.instance)
        return self._cached_value
    
    def __repr__(self):
        return f"<AsyncReferenceProxy: {self.field.name} (unfetched)>"
```

### 2. LazyReferenceField Async Support

#### Current LazyReference Class Enhancement
```python
class LazyReference(DBRef):
    # Add async fetch method
    async def async_fetch(self, force=False):
        """Async version of fetch()."""
        if self._cached_doc is None or force:
            collection = self.document_type._get_collection()
            doc = await collection.find_one({"_id": self.id})
            if doc is None:
                raise DoesNotExist(f"Trying to dereference unknown document {self}")
            self._cached_doc = self.document_type._from_son(doc)
        return self._cached_doc
```

### 3. GridFS Async Support

**Note**: Using PyMongo's native async GridFS API (gridfs.asynchronous) instead of Motor, as async support is now built into PyMongo.

#### FileField Async Methods
```python
class FileField(BaseField):
    async def async_put(self, file_obj, instance=None, **kwargs):
        """Async version of put() for storing files."""
        if is_sync_connection():
            raise RuntimeError("Use put() with sync connection")
        
        # Use PyMongo's AsyncGridFSBucket
        from gridfs.asynchronous import AsyncGridFSBucket
        db = get_db(self.db_alias)
        bucket = AsyncGridFSBucket(db, bucket_name=self.collection_name)
        
        # Store file asynchronously
        file_id = await bucket.upload_from_stream(
            kwargs.get('filename', 'unknown'),
            file_obj,
            metadata=kwargs.get('metadata')
        )
        
        return GridFSProxy(grid_id=file_id, collection_name=self.collection_name, 
                          key=self.name, instance=instance, db_alias=self.db_alias)
    
    async def async_get(self, instance):
        """Async version of get() for retrieving files."""
        grid_id = instance._data.get(self.name)
        if grid_id:
            return await AsyncGridFSProxy(
                grid_id=grid_id,
                collection_name=self.collection_name,
                db_alias=self.db_alias
            ).async_read()
        return None
```

#### AsyncGridFSProxy
```python
class AsyncGridFSProxy:
    """Async proxy for GridFS file operations."""
    
    async def async_read(self):
        """Read file content asynchronously."""
        from gridfs.asynchronous import AsyncGridFSBucket
        db = get_db(self.db_alias)
        bucket = AsyncGridFSBucket(db, bucket_name=self.collection_name)
        
        # Download to stream
        stream = io.BytesIO()
        await bucket.download_to_stream(self.grid_id, stream)
        stream.seek(0)
        return stream.read()
    
    async def async_delete(self):
        """Delete file asynchronously."""
        from gridfs.asynchronous import AsyncGridFSBucket
        db = get_db(self.db_alias)
        bucket = AsyncGridFSBucket(db, bucket_name=self.collection_name)
        await bucket.delete(self.grid_id)
```

### 4. Cascade Operations Async Support

#### Async Cascade Delete
```python
async def _async_handle_cascade_delete_rules(queryset, doc):
    """Async version of cascade delete operations."""
    delete_rules = doc._meta.get("delete_rules") or {}
    
    for field_name, rule in delete_rules.items():
        field = doc._fields[field_name]
        
        if rule == CASCADE:
            # Recursively delete referenced documents
            if isinstance(field, ReferenceField):
                ref_doc = await field.async_fetch(doc)
                if ref_doc:
                    await ref_doc.async_delete()
            elif isinstance(field, ListField) and isinstance(field.field, ReferenceField):
                ref_docs = await _async_fetch_list_references(doc, field)
                for ref_doc in ref_docs:
                    await ref_doc.async_delete()
        
        elif rule == NULLIFY:
            # Set references to null
            await _async_nullify_references(doc, field)
        
        elif rule == PULL:
            # Remove from list fields
            await _async_pull_from_lists(doc, field)
        
        elif rule == DENY:
            # Check if references exist
            if await _async_check_references_exist(doc, field):
                raise OperationError("Cannot delete document with references")
```

## Implementation Plan

### Step 1: Core Async Reference Support (Week 1)
- [ ] Implement `async_fetch()` method for ReferenceField
- [ ] Create AsyncReferenceProxy class
- [ ] Add `_async_lazy_load_ref()` static method
- [ ] Update connection detection in `__get__` method
- [ ] Write comprehensive tests for async dereferencing

### Step 2: LazyReferenceField Enhancement (Week 1-2)
- [ ] Add `async_fetch()` to LazyReference class
- [ ] Ensure compatibility with passthrough mode
- [ ] Test caching behavior in async context
- [ ] Document usage patterns

### Step 3: GridFS Async Implementation (Week 2)
- [ ] Implement FileField `async_put()` and `async_get()` methods
- [ ] Create AsyncGridFSProxy class
- [ ] Add ImageField async support (extends FileField)
- [ ] Implement streaming support for large files
- [ ] Test file upload/download operations

### Step 4: Cascade Operations (Week 2-3)
- [ ] Implement async cascade delete logic
- [ ] Add async support for NULLIFY, PULL, DENY rules
- [ ] Ensure proper transaction handling
- [ ] Test complex cascade scenarios

### Step 5: Integration and Testing (Week 3)
- [ ] Integration tests with complex document relationships
- [ ] Performance benchmarks comparing sync vs async
- [ ] Edge case handling (circular references, missing documents)
- [ ] Documentation and examples

## Usage Examples

### ReferenceField Async Usage
```python
class Author(Document):
    name = StringField()

class Book(Document):
    title = StringField()
    author = ReferenceField(Author)

# Async context
async def get_book_with_author():
    book = await Book.objects.async_first()
    
    # In async context, author is an AsyncReferenceProxy
    author_proxy = book.author
    print(author_proxy)  # <AsyncReferenceProxy: author (unfetched)>
    
    # Explicitly fetch the author
    author = await author_proxy.fetch()
    print(author.name)  # "John Doe"
    
    # Alternative: use field's async_fetch directly
    author = await Book.author.async_fetch(book)
```

### LazyReferenceField Async Usage
```python
class Post(Document):
    title = StringField()
    author = LazyReferenceField(Author)

post = await Post.objects.async_first()
# LazyReference object is returned
lazy_ref = post.author

# Async fetch when needed
author = await lazy_ref.async_fetch()
```

### GridFS Async Usage
```python
class Photo(Document):
    name = StringField()
    image = FileField()

# Upload file
photo = Photo(name="sunset.jpg")
with open("sunset.jpg", "rb") as f:
    await photo.image.async_put(f, filename="sunset.jpg")
await photo.async_save()

# Download file
photo = await Photo.objects.async_get(name="sunset.jpg")
image_data = await photo.image.async_get(photo)

# Alternative using PyMongo's async GridFS directly
from gridfs.asynchronous import AsyncGridFSBucket
db = photo._get_db()
bucket = AsyncGridFSBucket(db)

# List all files
async for grid_out in bucket.find():
    print(f"File: {grid_out.filename}, Size: {grid_out.length}")
```

## Testing Strategy

1. **Unit Tests**: Test each async method in isolation
2. **Integration Tests**: Test complete workflows with references
3. **Performance Tests**: Compare sync vs async performance
4. **Edge Cases**: 
   - Missing referenced documents
   - Circular references
   - Large file handling
   - Concurrent access

## Success Criteria

- [ ] All async field operations work correctly
- [ ] No regression in sync functionality
- [ ] Clear error messages for connection type mismatches
- [ ] Performance improvement in I/O-bound scenarios
- [ ] Comprehensive test coverage (>90%)
- [ ] Complete documentation with examples

## Notes

- Maintain backward compatibility - sync code must work unchanged
- Use consistent `async_` prefix for all async methods
- Ensure proper session handling for transactions
- Consider connection pooling implications
- Handle edge cases gracefully with clear error messages