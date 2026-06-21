import sys
from unittest.mock import MagicMock
# Mock psycopg2 before importing batch_writer
sys.modules['psycopg2'] = MagicMock()
sys.path.append('backend')
from batch_writer import BatchWriter

def test_batch_writer():
    print("Testing BatchWriter buffering and aggregation logic...")
    
    db_calls = []
    redis_invalidations = []
    
    def dummy_get_db_connection():
        # Returns a dummy object that logs executed SQL statements
        class DummyCursor:
            def execute(self, sql):
                db_calls.append(sql)
            def close(self):
                pass
            def mogrify(self, template, params):
                res = template
                for val in params:
                    if isinstance(val, str):
                        res = res.replace("%s", f"'{val}'", 1)
                    else:
                        res = res.replace("%s", str(val), 1)
                return res.encode('utf-8')
        class DummyConn:
            def cursor(self):
                return DummyCursor()
            def commit(self):
                pass
            def rollback(self):
                pass
            def close(self):
                pass
        return DummyConn()
        
    class DummyRedisClient:
        def __init__(self, name):
            self.name = name
        def delete(self, key):
            redis_invalidations.append((self.name, key))
            
    # Mock node lookup
    redis_clients = {
        "redis-1": DummyRedisClient("redis-1"),
        "redis-2": DummyRedisClient("redis-2"),
    }
    
    def dummy_get_redis_node(prefix):
        # Route to redis-1 if length of prefix is odd, else redis-2
        if len(prefix) % 2 == 1:
            return redis_clients["redis-1"]
        return redis_clients["redis-2"]

    # Initialize batch writer (flush interval 9999 to disable auto background flush during test)
    writer = BatchWriter(
        get_db_connection_fn=dummy_get_db_connection,
        get_redis_node_fn=dummy_get_redis_node,
        flush_interval=9999,
        max_batch_size=10
    )
    
    # Simulate search submissions
    writer.add_search("apple")
    writer.add_search("banana")
    writer.add_search("apple") # duplicate
    writer.add_search("cherry")
    writer.add_search("apple") # duplicate
    
    # Assert buffer status
    status = writer.get_status()
    print(f"Buffer Size: {status['buffer_size']}")
    assert status["buffer_size"] == 3, "Buffer should contain exactly 3 unique queries!"
    
    with writer.lock:
        apple_count, _ = writer.buffer["apple"]
        banana_count, _ = writer.buffer["banana"]
        cherry_count, _ = writer.buffer["cherry"]
        
    assert apple_count == 3, "Apple count should be aggregated to 3!"
    assert banana_count == 1, "Banana count should be 1!"
    assert cherry_count == 1, "Cherry count should be 1!"
    
    print("Buffer aggregation matches expectations!")
    
    # Trigger manual flush
    print("Triggering database and cache invalidation flush...")
    writer.flush()
    
    # Verify database calls
    assert len(db_calls) == 1, "Should have executed 1 database update transaction!"
    print(f"Executed SQL: {db_calls[0]}")
    assert "apple" in db_calls[0] and "banana" in db_calls[0] and "cherry" in db_calls[0], "SQL should insert all buffered queries!"
    
    # Verify cache invalidations
    print(f"Cache Invalidation list: {redis_invalidations}")
    # Apple prefixes: app, appl, apple -> invalidates basic/recency keys for each
    # Total invalidations should be 3 prefixes * 2 modes = 6 for apple, etc.
    assert len(redis_invalidations) > 0, "Redis invalidation keys should be deleted!"
    
    # Verify buffer is cleared after flush
    status_after = writer.get_status()
    assert status_after["buffer_size"] == 0, "Buffer should be empty after flush!"
    
    print("\nBatchWriter buffering, aggregation, and cache invalidation test passed successfully!")

if __name__ == "__main__":
    test_batch_writer()
