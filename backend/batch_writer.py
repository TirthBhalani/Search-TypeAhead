import time
import threading
from datetime import datetime
import psycopg2
from typing import Dict, Tuple

class BatchWriter:
    def __init__(self, get_db_connection_fn, get_redis_node_fn, flush_interval: int = 10, max_batch_size: int = 100):
        """
        get_db_connection_fn: function returning a psycopg2 connection
        get_redis_node_fn: function taking a prefix and returning the Redis client instance for that node
        flush_interval: interval in seconds between periodic flushes
        max_batch_size: maximum queries in buffer before triggering auto-flush
        """
        self.get_db_connection = get_db_connection_fn
        self.get_redis_node = get_redis_node_fn
        self.flush_interval = flush_interval
        self.max_batch_size = max_batch_size
        
        # Buffer structure: query -> (count_increment, latest_timestamp)
        self.buffer: Dict[str, Tuple[int, datetime]] = {}
        self.lock = threading.Lock()
        
        self.running = True
        self.flush_thread = threading.Thread(target=self._periodic_flush, daemon=True)
        
        # Metrics for dashboard
        self.total_db_writes = 0
        self.last_flush_time = time.time()
        self.flush_count = 0

    def start(self):
        """Start the background thread for periodic flushing."""
        self.flush_thread.start()
        print("BatchWriter background flush thread started.")

    def stop(self):
        """Stop the background thread and flush remaining buffer."""
        self.running = False
        self.flush()
        print("BatchWriter background flush thread stopped and flushed.")

    def add_search(self, query: str):
        """Add a search query to the buffer. Triggers flush if max_batch_size is reached."""
        now = datetime.now()
        should_flush = False
        
        with self.lock:
            if query in self.buffer:
                count, _ = self.buffer[query]
                self.buffer[query] = (count + 1, now)
            else:
                self.buffer[query] = (1, now)
                
            if len(self.buffer) >= self.max_batch_size:
                should_flush = True
                
        if should_flush:
            print(f"Batch size {len(self.buffer)} reached maximum limit. Triggering flush.")
            self.flush()

    def flush(self):
        """Flush all buffered counts to PostgreSQL and invalidate respective Redis caches."""
        with self.lock:
            if not self.buffer:
                self.last_flush_time = time.time()
                return
            
            # Take a snapshot of buffer and clear it
            snapshot = self.buffer
            self.buffer = {}

        # 1. PostgreSQL Bulk Upsert
        conn = None
        cur = None
        try:
            conn = self.get_db_connection()
            cur = conn.cursor()
            
            # Group records for insertion
            # queries columns: query, count, last_searched_at
            data_list = []
            for query, (count_inc, last_time) in snapshot.items():
                data_list.append((query, count_inc, last_time))
                
            # Perform upsert in a single bulk transaction
            # Note: Postgres allows execute_values or manual batch upsert
            # We will construct a query with placeholders
            print(f"Flushing {len(data_list)} queries to PostgreSQL...")
            
            # Using execute_values is standard and secure, but to avoid extra imports we can batch manually
            # or use simple cursor parameters
            args_str = ",".join(cur.mogrify("(%s, %s, %s)", x).decode('utf-8') for x in data_list)
            upsert_query = f"""
                INSERT INTO queries (query, count, last_searched_at)
                VALUES {args_str}
                ON CONFLICT (query)
                DO UPDATE SET
                    count = queries.count + EXCLUDED.count,
                    last_searched_at = EXCLUDED.last_searched_at;
            """
            cur.execute(upsert_query)
            conn.commit()
            
            self.total_db_writes += len(data_list)
            self.flush_count += 1
            print(f"Successfully flushed batch. Total queries updated in DB: {len(data_list)}")
            
        except Exception as e:
            print(f"Error during BatchWriter database flush: {e}")
            if conn:
                conn.rollback()
            # Restore items to buffer to avoid data loss
            with self.lock:
                for query, (count_inc, last_time) in snapshot.items():
                    if query in self.buffer:
                        curr_inc, curr_time = self.buffer[query]
                        self.buffer[query] = (curr_inc + count_inc, max(curr_time, last_time))
                    else:
                        self.buffer[query] = (count_inc, last_time)
            return
        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()

        # 2. Redis Cache Invalidation
        # For each query in the batch, invalidate all of its prefixes (length >= 3)
        invalidated_keys = 0
        for query in snapshot.keys():
            # Get all prefix substrings of the query that can trigger suggestions
            # Example: "iphone" -> "iph", "ipho", "iphon", "iphone"
            prefixes = [query[:i] for i in range(3, len(query) + 1)]
            
            for prefix in prefixes:
                # Find which Redis node is responsible for this prefix
                redis_node = self.get_redis_node(prefix)
                if redis_node:
                    # Invalidate both basic and recency suggestion caches
                    key_basic = f"suggest:basic:{prefix}"
                    key_recency = f"suggest:recency:{prefix}"
                    try:
                        # Delete keys (non-blocking is fine)
                        redis_node.delete(key_basic)
                        redis_node.delete(key_recency)
                        invalidated_keys += 2
                    except Exception as e:
                        print(f"Failed to invalidate cache key on Redis for prefix '{prefix}': {e}")
                        
        # 3. Invalidate Trending Cache Key
        redis_node_trending = self.get_redis_node("trending")
        if redis_node_trending:
            try:
                redis_node_trending.delete("suggest:trending")
                invalidated_keys += 1
            except Exception as e:
                print(f"Failed to invalidate trending cache key: {e}")
                
        if invalidated_keys > 0:
            print(f"Invalidated {invalidated_keys} cache keys (including trending) across the Redis cluster.")
            
        self.last_flush_time = time.time()

    def _periodic_flush(self):
        """Target for periodic background thread execution."""
        while self.running:
            time.sleep(1)
            # Check if flush interval has elapsed
            if time.time() - self.last_flush_time >= self.flush_interval:
                self.flush()

    def get_status(self) -> dict:
        """Returns metadata status of the BatchWriter."""
        with self.lock:
            buffer_len = len(self.buffer)
        return {
            "buffer_size": buffer_len,
            "total_db_writes": self.total_db_writes,
            "flush_count": self.flush_count,
            "seconds_since_last_flush": int(time.time() - self.last_flush_time),
            "flush_interval": self.flush_interval
        }
