import os
import sys
import time
import psycopg2

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "typeahead_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres_pass")
CLEANED_DATA_PATH = "/app/data/cleaned_queries.csv"

# If running locally (not inside docker), map path
if not os.path.exists(CLEANED_DATA_PATH):
    CLEANED_DATA_PATH = "data/cleaned_queries.csv"

def get_connection():
    retries = 10
    conn = None
    while retries > 0:
        try:
            conn = psycopg2.connect(
                host=DB_HOST,
                port=DB_PORT,
                database=DB_NAME,
                user=DB_USER,
                password=DB_PASSWORD
            )
            print("Successfully connected to PostgreSQL database.")
            return conn
        except psycopg2.OperationalError as e:
            print(f"PostgreSQL not ready yet, retrying... ({retries} retries left)")
            retries -= 1
            time.sleep(2)
    print("Could not connect to database after several retries.")
    sys.exit(1)

def init_db():
    conn = get_connection()
    cur = conn.cursor()
    
    # Check if table queries exists
    cur.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_name = 'queries'
        );
    """)
    table_exists = cur.fetchone()[0]
    
    row_count = 0
    if table_exists:
        cur.execute("SELECT COUNT(*) FROM queries;")
        row_count = cur.fetchone()[0]
        print(f"Current query count in DB: {row_count}")
        
        # If count is less than 100,000, drop the table to force recreation with clean TEXT types
        if row_count < 100000:
            print("Recreating table queries to ensure clean schema and text type definitions...")
            cur.execute("DROP TABLE IF EXISTS queries CASCADE;")
            conn.commit()
            table_exists = False

    if not table_exists:
        # Create Table
        print("Creating schema...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS queries (
                id SERIAL PRIMARY KEY,
                query TEXT UNIQUE NOT NULL,
                count BIGINT NOT NULL DEFAULT 0,
                last_searched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # Create Indices for performance
        print("Creating indexes...")
        # text_pattern_ops speeds up LIKE 'prefix%' queries on TEXT columns
        cur.execute("CREATE INDEX IF NOT EXISTS idx_queries_query_pattern ON queries (query text_pattern_ops);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_queries_count ON queries (count DESC);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_queries_last_searched ON queries (last_searched_at DESC);")
        conn.commit()
        
    # Check current row count
    cur.execute("SELECT COUNT(*) FROM queries;")
    row_count = cur.fetchone()[0]
    
    if row_count >= 100000:
        print("Database already contains query records. Ingestion skipped.")
        cur.close()
        conn.close()
        return

    # Ingest from CSV
    if not os.path.exists(CLEANED_DATA_PATH):
        print(f"Cleaned dataset CSV not found at {CLEANED_DATA_PATH}. Skipping ingestion.")
        cur.close()
        conn.close()
        return

    print(f"Loading cleaned dataset from {CLEANED_DATA_PATH} using COPY...")
    start_time = time.time()
    try:
        # Clear existing data to avoid conflict during initial load
        cur.execute("TRUNCATE TABLE queries;")
        conn.commit()
        
        with open(CLEANED_DATA_PATH, "r", encoding="utf-8") as f:
            # We use copy_expert because it is incredibly fast (under 1 sec for 100k records)
            sql = "COPY queries(query, count, last_searched_at) FROM STDIN WITH CSV HEADER ESCAPE '\"';"
            cur.copy_expert(sql, f)
            conn.commit()
            
        print("Shifting historical query times to align with current time...")
        cur.execute("""
            UPDATE queries 
            SET last_searched_at = last_searched_at + (NOW() - (SELECT MAX(last_searched_at) FROM queries));
        """)
        conn.commit()
        print(f"Successfully loaded and shifted data in {time.time() - start_time:.2f} seconds.")
    except Exception as e:
        print(f"Error occurred during copy: {e}")
        conn.rollback()
        
    cur.execute("SELECT COUNT(*) FROM queries;")
    final_count = cur.fetchone()[0]
    print(f"Final query count in DB: {final_count}")
    
    cur.close()
    conn.close()

if __name__ == "__main__":
    init_db()
