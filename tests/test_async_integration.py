"""Integration tests for async functionality."""

import pytest
from datetime import datetime

from mongoengine import (
    Document,
    StringField,
    IntField,
    DateTimeField,
    ReferenceField,
    ListField,
    connect_async,
    disconnect_async,
)


class User(Document):
    """User model for testing."""
    username = StringField(required=True, unique=True)
    email = StringField(required=True)
    age = IntField(min_value=0)
    created_at = DateTimeField(default=datetime.utcnow)
    
    meta = {
        "collection": "async_users",
        "indexes": [
            "username",
            "email",
        ]
    }


class BlogPost(Document):
    """Blog post model for testing."""
    title = StringField(required=True, max_length=200)
    content = StringField(required=True)
    author = ReferenceField(User, required=True)
    tags = ListField(StringField(max_length=50))
    created_at = DateTimeField(default=datetime.utcnow)
    
    meta = {
        "collection": "async_blog_posts",
        "indexes": [
            "author",
            ("created_at", "-1"),  # Descending index on created_at
        ]
    }


@pytest.mark.asyncio
class TestAsyncIntegration:
    """Test complete async workflow."""

    async def setup_method(self, method):
        """Set up test database."""
        await connect_async(db="test_async_integration", alias="async_test")
        User._meta["db_alias"] = "async_test"
        BlogPost._meta["db_alias"] = "async_test"
        
        # Ensure indexes
        await User.async_ensure_indexes()
        await BlogPost.async_ensure_indexes()

    async def teardown_method(self, method):
        """Clean up test database."""
        try:
            await User.async_drop_collection()
            await BlogPost.async_drop_collection()
        except:
            pass
        await disconnect_async("async_test")

    async def test_complete_workflow(self):
        """Test a complete async workflow with users and blog posts."""
        # Create users
        user1 = User(username="alice", email="alice@example.com", age=25)
        user2 = User(username="bob", email="bob@example.com", age=30)
        
        await user1.async_save()
        await user2.async_save()
        
        # Create blog posts
        post1 = BlogPost(
            title="Introduction to Async MongoDB",
            content="Async MongoDB with MongoEngine is great!",
            author=user1,
            tags=["mongodb", "async", "python"]
        )
        
        post2 = BlogPost(
            title="Advanced Async Patterns",
            content="Let's explore advanced async patterns...",
            author=user1,
            tags=["async", "patterns"]
        )
        
        post3 = BlogPost(
            title="Bob's First Post",
            content="Hello from Bob!",
            author=user2,
            tags=["introduction"]
        )
        
        # Save posts
        await post1.async_save()
        await post2.async_save()
        await post3.async_save()
        
        # Test reload
        await user1.async_reload()
        assert user1.username == "alice"
        
        # Update user
        user1.age = 26
        await user1.async_save()
        
        # Reload and verify update
        await user1.async_reload()
        assert user1.age == 26
        
        # Test cascade operations
        post4 = BlogPost(
            title="Cascade Test",
            content="Testing cascade save",
            author=User(username="charlie", email="charlie@example.com", age=35),
            tags=["test"]
        )
        
        # Save with cascade - should save the new user too
        await post4.async_save(cascade=True)
        assert post4.author.id is not None
        
        # Verify the cascaded user was saved
        collection = await User._async_get_collection()
        charlie = await collection.find_one({"username": "charlie"})
        assert charlie is not None
        
        # Delete a user
        await user2.async_delete()
        
        # Verify deletion
        collection = await User._async_get_collection()
        deleted_user = await collection.find_one({"username": "bob"})
        assert deleted_user is None

    async def test_concurrent_operations(self):
        """Test concurrent async operations."""
        import asyncio
        
        # Create multiple users concurrently
        users = [
            User(username=f"user{i}", email=f"user{i}@example.com", age=20+i)
            for i in range(10)
        ]
        
        # Save all users concurrently
        await asyncio.gather(
            *[user.async_save() for user in users]
        )
        
        # Verify all were saved
        collection = await User._async_get_collection()
        count = await collection.count_documents({})
        assert count == 10
        
        # Update all users concurrently
        for user in users:
            user.age += 1
        
        await asyncio.gather(
            *[user.async_save() for user in users]
        )
        
        # Reload and verify updates
        await asyncio.gather(
            *[user.async_reload() for user in users]
        )
        
        for i, user in enumerate(users):
            assert user.age == 21 + i
        
        # Delete all users concurrently
        await asyncio.gather(
            *[user.async_delete() for user in users]
        )
        
        # Verify all were deleted
        count = await collection.count_documents({})
        assert count == 0

    async def test_error_handling(self):
        """Test error handling in async operations."""
        # Test unique constraint
        user1 = User(username="duplicate", email="dup1@example.com")
        await user1.async_save()
        
        user2 = User(username="duplicate", email="dup2@example.com")
        with pytest.raises(Exception):  # Should raise duplicate key error
            await user2.async_save()
        
        # Test required field validation
        invalid_post = BlogPost(title="No Content")  # Missing required content
        with pytest.raises(Exception):
            await invalid_post.async_save()
        
        # Test reference validation
        unsaved_user = User(username="unsaved", email="unsaved@example.com")
        post = BlogPost(
            title="Invalid Reference",
            content="This should fail",
            author=unsaved_user  # Reference to unsaved document
        )
        
        # This should work with cascade
        await post.async_save(cascade=True)
        assert unsaved_user.id is not None