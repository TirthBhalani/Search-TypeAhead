import os
import json
import time
from contextlib import asynccontextmanager
import psycopg2
from psycopg2 import pool
import redis
from fastapi import FastAPI, HTTPException, Query, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from consistent_hash import ConsistentHashRing
from batch_writer import BatchWriter

# Environment configuration
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "typeahead_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres_pass")

REDIS_NODES_ENV = os.getenv("REDIS_NODES", "localhost:6379,localhost:6380,localhost:6381")

# Global variables
db_pool = None
redis_clients = {}
hash_ring = None
batch_writer = None

# Metrics tracking
metrics = {
    "cache_hits": 0,
    "cache_misses": 0,
    "db_reads": 0,
    "total_requests": 0,
    "response_times": [] # List of float response times in ms (last 100 requests)
}

def get_db_connection():
    """Get a connection from the pool."""
    if db_pool:
        return db_pool.getconn()
    else:
        # Fallback for manual local testing without pool
        return psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )

def release_db_connection(conn):
    """Release a connection back to the pool."""
    if db_pool:
        db_pool.putconn(conn)
    else:
        conn.close()

def get_redis_node_client(prefix: str):
    """Given a prefix, route to the correct Redis client using consistent hashing."""
    if not hash_ring or not redis_clients:
        return None
    node_name = hash_ring.get_node(prefix)
    return redis_clients.get(node_name)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool, redis_clients, hash_ring, batch_writer
    
    print("Starting up FastAPI application...")
    
    # 1. Initialize PostgreSQL Connection Pool
    try:
        db_pool = psycopg2.pool.SimpleConnectionPool(
            1, 20,
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )
        print("PostgreSQL connection pool initialized.")
    except Exception as e:
        print(f"Error initializing PostgreSQL connection pool: {e}")
        raise e

    # 2. Parse and Initialize Redis clients
    nodes_list = []
    # REDIS_NODES_ENV format: "redis-1:6379,redis-2:6379,redis-3:6379"
    # Or for local fallback: "localhost:6379,localhost:6380,localhost:6381"
    for item in REDIS_NODES_ENV.split(","):
        parts = item.strip().split(":")
        if len(parts) == 2:
            host, port = parts[0], int(parts[1])
            # If running locally (not inside docker network), we can map node names to localhost with different ports
            node_key = host
            
            # Create client connection
            try:
                # We use socket_keepalive to avoid connection drops
                client = redis.Redis(
                    host=host, 
                    port=port, 
                    decode_responses=True,
                    socket_connect_timeout=3
                )
                client.ping()
                redis_clients[node_key] = client
                nodes_list.append(node_key)
                print(f"Connected to Redis node: {node_key} ({host}:{port})")
            except Exception as ex:
                print(f"Warning: Failed to connect to Redis node {node_key} at {host}:{port}: {ex}")
                # For safety, let's still add the node key even if ping fails, so hashing structure remains
                # Or fallback to localhost mapping if hostname fails
                if host != "localhost" and os.getenv("ENV") != "production":
                    try:
                        # Map docker hostname to localhost for local testing outside docker
                        local_port = 6379 if "redis-1" in host else (6380 if "redis-2" in host else 6381)
                        client = redis.Redis(host="localhost", port=local_port, decode_responses=True)
                        client.ping()
                        redis_clients[node_key] = client
                        nodes_list.append(node_key)
                        print(f"Connected to local fallback Redis node: {node_key} (localhost:{local_port})")
                    except Exception:
                        pass

    # 3. Initialize Hashing Ring (100 virtual nodes per physical node)
    hash_ring = ConsistentHashRing(nodes=nodes_list, virtual_nodes=100)
    print(f"Consistent Hashing Ring initialized with nodes: {nodes_list}")

    # 4. Initialize and Start BatchWriter
    batch_writer = BatchWriter(
        get_db_connection_fn=get_db_connection,
        get_redis_node_fn=get_redis_node_client,
        flush_interval=10,
        max_batch_size=50
    )
    batch_writer.start()
    
    yield
    
    # Shutdown logic
    print("Shutting down FastAPI application...")
    if batch_writer:
        batch_writer.stop()
    if db_pool:
        db_pool.closeall()
        print("PostgreSQL connection pool closed.")

app = FastAPI(lifespan=lifespan, title="Search Typeahead API")

# Allow CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class SearchRequest(BaseModel):
    query: str

@app.get("/suggest")
def suggest(q: str = Query("", description="Prefix to get suggestions for"), mode: str = Query("basic", description="Ranking mode: basic or recency")):
    start_time = time.time()
    metrics["total_requests"] += 1
    
    # 1. Clean query
    prefix = q.strip().lower()
    
    # 2. Return empty if length < 3
    if len(prefix) < 3:
        # Track response time
        resp_time = (time.time() - start_time) * 1000
        metrics["response_times"].append(resp_time)
        if len(metrics["response_times"]) > 100:
            metrics["response_times"].pop(0)
        return []
        
    # 3. Query Hash Ring for node client
    redis_client = get_redis_node_client(prefix)
    
    cache_key = f"suggest:{mode}:{prefix}"
    cached_data = None
    
    if redis_client:
        try:
            cached_data = redis_client.get(cache_key)
        except Exception as e:
            print(f"Redis get failed: {e}")
            
    # 4. Cache Hit
    if cached_data:
        metrics["cache_hits"] += 1
        results = json.loads(cached_data)
        
        # Track response time
        resp_time = (time.time() - start_time) * 1000
        metrics["response_times"].append(resp_time)
        if len(metrics["response_times"]) > 100:
            metrics["response_times"].pop(0)
            
        return results
        
    # 5. Cache Miss -> Query PostgreSQL
    metrics["cache_misses"] += 1
    metrics["db_reads"] += 1
    
    conn = None
    cur = None
    results = []
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Search queries matching prefix
        like_pattern = f"{prefix}%"
        
        if mode == "recency":
            # Recency-weighted decay scoring formula:
            # score = count * EXP(-0.02 * days_since_last_searched)
            # Higher recency queries will rank higher
            query_sql = """
                SELECT query, count, last_searched_at,
                       (count * EXP(-0.02 * EXTRACT(EPOCH FROM (NOW() - last_searched_at)) / 86400.0)) AS score
                FROM queries
                WHERE query LIKE %s
                ORDER BY score DESC, count DESC
                LIMIT 10;
            """
        else:
            # Basic mode: sort strictly by count descending
            query_sql = """
                SELECT query, count, last_searched_at
                FROM queries
                WHERE query LIKE %s
                ORDER BY count DESC
                LIMIT 10;
            """
            
        cur.execute(query_sql, (like_pattern,))
        rows = cur.fetchall()
        
        # Format list
        for row in rows:
            results.append({
                "query": row[0],
                "count": row[1],
                "last_searched_at": row[2].strftime("%Y-%m-%d %H:%M:%S")
            })
            
    except Exception as e:
        print(f"Database query error in /suggest: {e}")
        # Rollback in case of transaction issues
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail="Database lookup error")
    finally:
        if cur:
            cur.close()
        if conn:
            release_db_connection(conn)

    # 6. Save in Redis with 1-hour TTL (3600 seconds)
    if redis_client:
        try:
            redis_client.setex(cache_key, 3600, json.dumps(results))
        except Exception as e:
            print(f"Redis setex failed: {e}")
            
    # Track response time
    resp_time = (time.time() - start_time) * 1000
    metrics["response_times"].append(resp_time)
    if len(metrics["response_times"]) > 100:
        metrics["response_times"].pop(0)
        
    return results

@app.get("/trending")
def get_trending(limit: int = Query(7, description="Number of trending searches to return")):
    metrics["total_requests"] += 1
    
    # Query Hash Ring for routing key "trending" to route to the correct Redis node
    redis_client = get_redis_node_client("trending")
    cache_key = "suggest:trending"
    cached_data = None
    
    if redis_client:
        try:
            cached_data = redis_client.get(cache_key)
        except Exception as e:
            print(f"Redis get failed for trending: {e}")
            
    if cached_data:
        metrics["cache_hits"] += 1
        return json.loads(cached_data)
        
    # Cache Miss -> Query PostgreSQL
    metrics["cache_misses"] += 1
    metrics["db_reads"] += 1
    
    conn = None
    cur = None
    results = []
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        # Fetch top queries sorted by recency-decay score
        cur.execute("""
            SELECT query
            FROM queries
            ORDER BY (count * EXP(-0.05 * EXTRACT(EPOCH FROM (NOW() - last_searched_at)) / 86400.0)) DESC, count DESC
            LIMIT %s;
        """, (limit,))
        rows = cur.fetchall()
        results = [row[0] for row in rows]
    except Exception as e:
        print(f"Database query error in /trending: {e}")
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail="Database lookup error")
    finally:
        if cur:
            cur.close()
        if conn:
            release_db_connection(conn)
            
    # Cache in Redis with a 60-second TTL
    if redis_client and results:
        try:
            redis_client.setex(cache_key, 60, json.dumps(results))
        except Exception as e:
            print(f"Redis setex failed for trending: {e}")
            
    return results

@app.post("/search")
def search(request: SearchRequest):
    query_str = request.query.strip().lower()
    
    # Perform sanity check
    if not query_str or len(query_str) < 3:
        raise HTTPException(status_code=400, detail="Query too short. Minimum 3 characters.")
        
    # Queue search in BatchWriter
    if batch_writer:
        batch_writer.add_search(query_str)
        return {"message": "Searched"}
    else:
        # Fallback if BatchWriter is not started
        raise HTTPException(status_code=503, detail="Search indexing service unavailable")

@app.get("/cache/debug")
def cache_debug(prefix: str = Query(..., description="Prefix to inspect")):
    prefix_clean = prefix.strip().lower()
    if len(prefix_clean) < 3:
        raise HTTPException(status_code=400, detail="Prefix must be at least 3 characters")
        
    if not hash_ring:
        raise HTTPException(status_code=503, detail="Hash ring unavailable")
        
    node = hash_ring.get_node(prefix_clean)
    
    # Check if cached in basic or recency mode
    redis_client = redis_clients.get(node)
    
    basic_cached = False
    recency_cached = False
    
    if redis_client:
        try:
            basic_cached = redis_client.exists(f"suggest:basic:{prefix_clean}") > 0
            recency_cached = redis_client.exists(f"suggest:recency:{prefix_clean}") > 0
        except Exception as e:
            print(f"Failed to query Redis existence: {e}")
            
    # Hash ring debug values
    virtual_nodes_on_ring = len(hash_ring.sorted_keys)
    prefix_hash = hash_ring._hash(prefix_clean)
    
    return {
        "prefix": prefix_clean,
        "prefix_hash": prefix_hash,
        "mapped_node": node,
        "basic_cache_status": "HIT" if basic_cached else "MISS",
        "recency_cache_status": "HIT" if recency_cached else "MISS",
        "hash_ring": {
            "total_nodes": len(redis_clients),
            "total_virtual_nodes": virtual_nodes_on_ring
        }
    }

@app.get("/metrics")
def get_metrics():
    # Compute P95 latency
    p95_latency = 0.0
    avg_latency = 0.0
    if metrics["response_times"]:
        sorted_times = sorted(metrics["response_times"])
        p95_idx = int(len(sorted_times) * 0.95)
        p95_latency = sorted_times[min(p95_idx, len(sorted_times) - 1)]
        avg_latency = sum(sorted_times) / len(sorted_times)
        
    # Get BatchWriter status
    batch_status = batch_writer.get_status() if batch_writer else {}
    
    # Calculate Cache Hit Rate
    total_lookups = metrics["cache_hits"] + metrics["cache_misses"]
    hit_rate = (metrics["cache_hits"] / total_lookups * 100) if total_lookups > 0 else 0.0
    
    # Get key counts for each Redis node
    redis_keys = {}
    for node_name, client in redis_clients.items():
        try:
            # dbsize counts all keys in database
            redis_keys[node_name] = client.dbsize()
        except Exception:
            redis_keys[node_name] = "Error"
            
    return {
        "cache_hits": metrics["cache_hits"],
        "cache_misses": metrics["cache_misses"],
        "cache_hit_rate_pct": round(hit_rate, 2),
        "db_reads": metrics["db_reads"],
        "db_writes": batch_status.get("total_db_writes", 0),
        "batch_buffer_size": batch_status.get("buffer_size", 0),
        "batch_flush_count": batch_status.get("flush_count", 0),
        "seconds_since_last_flush": batch_status.get("seconds_since_last_flush", 0),
        "avg_response_time_ms": round(avg_latency, 2),
        "p95_response_time_ms": round(p95_latency, 2),
        "redis_node_keys": redis_keys
    }

# Serving the static frontend
# Note: StaticFiles mounting must be done last to allow routing paths to match first
# If frontend directory exists, mount it
FRONTEND_DIR = "/app/frontend"
if not os.path.exists(FRONTEND_DIR):
    FRONTEND_DIR = "frontend"

if os.path.exists(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
else:
    print(f"Warning: Static frontend directory not found at {FRONTEND_DIR}")
