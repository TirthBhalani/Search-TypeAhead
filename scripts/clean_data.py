import os
import re
from datetime import datetime

RAW_DATA_PATH = "data/user-ct-test-collection-02.txt"
CLEANED_DATA_PATH = "data/cleaned_queries.csv"

# Comprehensive blacklist for filtering sexually explicit/implicit queries
SEXUAL_BLACKLIST = {
    "porn", "sex", "vagina", "mature", "adult", "teen", "lolita", "jizz", "slut", 
    "nude", "naked", "xxx", "boob", "penis", "orgasm", "erot", "blowjob", "anal", 
    "lesbian", "gay", "webcam", "incest", "milf", "pussy", "intercour", "cock", 
    "semen", "clitoris", "masturbat", "prostitution", "fetish", "bondage", "bdsm", 
    "strip", "escort", "arousal", "swinger", "whore", "slutty", "gangbang", "facial", 
    "cum", "squirting", "hentai", "ejacula", "blow job", "handjob", "ejaculation", 
    "dildo", "vibrator", "penetrat", "fantasies", "shemale", "transsex"
}

# Regex to check if a query contains any of the blacklisted words as sub-words
# e.g., "sex", "amateursex", "pornstar" etc.
BLACKLIST_REGEX = re.compile(
    r"\b(" + "|".join(SEXUAL_BLACKLIST) + r")\b|" +
    r"(" + "|".join(SEXUAL_BLACKLIST) + r")"
)

def clean_query(query: str) -> str:
    if not query:
        return ""
    # Lowercase
    q = query.lower()
    # Normalize whitespaces
    q = re.sub(r"\s+", " ", q).strip()
    
    # Exclude if it looks like a URL
    if q.startswith("http://") or q.startswith("https://") or q.startswith("www.") or q.endswith(".com") or q.endswith(".net") or q.endswith(".org"):
        return ""
        
    # Exclude if it's empty, too short (less than 3 chars), or just punctuation/garbage
    if len(q) < 3 or not re.search(r"[a-z0-9]", q):
        return ""
        
    # Check blacklist
    if BLACKLIST_REGEX.search(q):
        return ""
        
    return q

def process_dataset():
    print(f"Reading raw dataset from {RAW_DATA_PATH}...")
    if not os.path.exists(RAW_DATA_PATH):
        raise FileNotFoundError(f"Raw dataset file not found at {RAW_DATA_PATH}")

    query_counts = {}
    query_times = {}

    line_count = 0
    clean_count = 0
    filtered_count = 0

    with open(RAW_DATA_PATH, "r", encoding="utf-8", errors="ignore") as f:
        # Skip header: AnonID	Query	QueryTime	ItemRank	ClickURL
        header = f.readline()
        
        for line in f:
            line_count += 1
            if line_count % 500000 == 0:
                print(f"Processed {line_count} lines...")
                
            parts = line.split("\t")
            if len(parts) < 3:
                continue
                
            raw_query = parts[1]
            raw_time = parts[2]
            
            cleaned = clean_query(raw_query)
            if not cleaned:
                filtered_count += 1
                continue
                
            # Keep query count
            query_counts[cleaned] = query_counts.get(cleaned, 0) + 1
            
            # Keep track of latest search time for recency features
            try:
                dt = datetime.strptime(raw_time.strip(), "%Y-%m-%d %H:%M:%S")
                # Store the latest timestamp
                if cleaned not in query_times or dt > query_times[cleaned]:
                    query_times[cleaned] = dt
            except ValueError:
                # If time is malformed, skip time update but keep the query count
                pass
                
            clean_count += 1

    print(f"Finished parsing. Total lines: {line_count}")
    print(f"Total clean entries: {clean_count}, Filtered/Invalid entries: {filtered_count}")
    print(f"Unique clean queries: {len(query_counts)}")

    # Sort queries by frequency descending
    sorted_queries = sorted(query_counts.items(), key=lambda item: item[1], reverse=True)
    
    # We will export the top 100,000 queries
    export_limit = 100000
    print(f"Exporting top {export_limit} queries to {CLEANED_DATA_PATH}...")
    
    os.makedirs(os.path.dirname(CLEANED_DATA_PATH), exist_ok=True)
    with open(CLEANED_DATA_PATH, "w", encoding="utf-8") as out:
        out.write("query,count,last_searched_at\n")
        for query, count in sorted_queries[:export_limit]:
            # Get latest search time or use a default if missing
            dt = query_times.get(query, datetime(2006, 5, 31, 23, 59, 59))
            time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            # Escape commas in query for safe CSV representation
            escaped_query = query.replace('"', '""')
            out.write(f'"{escaped_query}",{count},{time_str}\n')
            
    print("Pre-processing and data cleaning complete!")

if __name__ == "__main__":
    process_dataset()
