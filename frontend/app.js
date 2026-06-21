// State Management
let currentMode = 'basic'; // 'basic' or 'recency'
let debounceTimer = null;
let currentSuggestions = [];
let selectedSuggestionIndex = -1;
let metricsInterval = null;

// DOM Elements
const searchInput = document.getElementById('search-input');
const searchClearBtn = document.getElementById('search-clear-btn');
const searchSubmitBtn = document.getElementById('search-submit-btn');
const suggestionsDropdown = document.getElementById('suggestions-dropdown');
const suggestionsList = document.getElementById('suggestions-list');
const resultsPanel = document.getElementById('search-results-panel');
const trendingTags = document.getElementById('trending-tags');

// Mode Buttons
const modeBasicBtn = document.getElementById('mode-basic-btn');
const modeRecencyBtn = document.getElementById('mode-recency-btn');

// Metrics Elements
const metricHitRate = document.getElementById('metric-hit-rate');
const metricHitRateBar = document.getElementById('metric-hit-rate-bar');
const metricHits = document.getElementById('metric-hits');
const metricMisses = document.getElementById('metric-misses');
const metricLatency = document.getElementById('metric-latency');
const metricAvgLatency = document.getElementById('metric-avg-latency');
const metricBufferSize = document.getElementById('metric-buffer-size');
const metricFlushCountdown = document.getElementById('metric-flush-countdown');
const metricFlushCount = document.getElementById('metric-flush-count');
const metricDbReads = document.getElementById('metric-db-reads');
const metricDbWrites = document.getElementById('metric-db-writes');
const metricWriteSaved = document.getElementById('metric-write-saved');

// Routing Debug Elements
const debugPrefix = document.getElementById('debug-prefix');
const debugHash = document.getElementById('debug-hash');
const debugNode = document.getElementById('debug-node');

// Init Function
document.addEventListener('DOMContentLoaded', () => {
    setupEventListeners();
    fetchTrendingTags();
    fetchMetrics();
    // Poll metrics every 1.5 seconds
    metricsInterval = setInterval(fetchMetrics, 1500);
    // Poll trending searches every 10 seconds
    setInterval(fetchTrendingTags, 10000);
});

// Setup Events
function setupEventListeners() {
    // Input keypress & input listeners
    searchInput.addEventListener('input', handleSearchInput);
    searchInput.addEventListener('keydown', handleSearchKeydown);
    
    // Clear & Submit Button clicks
    searchClearBtn.addEventListener('click', clearSearch);
    searchSubmitBtn.addEventListener('click', () => submitSearch(searchInput.value));
    
    // Toggle Mode listeners
    modeBasicBtn.addEventListener('click', () => setMode('basic'));
    modeRecencyBtn.addEventListener('click', () => setMode('recency'));
    
    // Document click to close dropdown when clicking outside
    document.addEventListener('click', (e) => {
        if (!searchInput.contains(e.target) && !suggestionsDropdown.contains(e.target)) {
            closeDropdown();
        }
    });
    
    // Focus in to show suggestions if they exist
    searchInput.addEventListener('focus', () => {
        if (searchInput.value.trim().length >= 3 && currentSuggestions.length > 0) {
            openDropdown();
        }
    });
}

// Mode Selection Handler
function setMode(mode) {
    if (currentMode === mode) return;
    currentMode = mode;
    
    if (mode === 'basic') {
        modeBasicBtn.classList.add('active');
        modeRecencyBtn.classList.remove('active');
        showToast('Switched to Basic Popularity Ranking');
    } else {
        modeBasicBtn.classList.remove('active');
        modeRecencyBtn.classList.add('active');
        showToast('Switched to Enhanced Recency-Aware Ranking');
    }
    
    // Re-fetch suggestions for current text
    if (searchInput.value.trim().length >= 3) {
        fetchSuggestions(searchInput.value.trim());
    }
}

// Debounced Input Handler
function handleSearchInput(e) {
    const value = e.target.value;
    
    // Toggle Clear button visibility
    if (value.length > 0) {
        searchClearBtn.classList.remove('hidden');
    } else {
        searchClearBtn.classList.add('hidden');
        closeDropdown();
    }
    
    // Hide search success panel on input change
    resultsPanel.classList.add('hidden');
    
    clearTimeout(debounceTimer);
    
    const prefix = value.trim();
    if (prefix.length < 3) {
        closeDropdown();
        resetDebugInfo();
        return;
    }
    
    // Debounce suggestions fetch at 150ms
    debounceTimer = setTimeout(() => {
        fetchSuggestions(prefix);
        fetchRoutingDebug(prefix);
    }, 150);
}

// Keyboard Navigation inside suggestions list
function handleSearchKeydown(e) {
    const listItems = suggestionsList.getElementsByClassName('suggestion-item');
    
    if (e.key === 'ArrowDown') {
        // Move selection down
        e.preventDefault();
        if (listItems.length === 0) return;
        
        selectedSuggestionIndex++;
        if (selectedSuggestionIndex >= listItems.length) {
            selectedSuggestionIndex = 0;
        }
        updateSuggestionHighlight(listItems);
    } 
    else if (e.key === 'ArrowUp') {
        // Move selection up
        e.preventDefault();
        if (listItems.length === 0) return;
        
        selectedSuggestionIndex--;
        if (selectedSuggestionIndex < 0) {
            selectedSuggestionIndex = listItems.length - 1;
        }
        updateSuggestionHighlight(listItems);
    } 
    else if (e.key === 'Enter') {
        // Submit search
        e.preventDefault();
        if (selectedSuggestionIndex >= 0 && selectedSuggestionIndex < currentSuggestions.length) {
            const selectedQuery = currentSuggestions[selectedSuggestionIndex].query;
            searchInput.value = selectedQuery;
            closeDropdown();
            submitSearch(selectedQuery);
        } else {
            submitSearch(searchInput.value);
        }
    } 
    else if (e.key === 'Escape') {
        // Close suggestions
        closeDropdown();
    }
}

// Update UI highlight for keyboard navigation
function updateSuggestionHighlight(listItems) {
    for (let i = 0; i < listItems.length; i++) {
        if (i === selectedSuggestionIndex) {
            listItems[i].classList.add('selected');
            // Auto scroll suggestion into view if needed
            listItems[i].scrollIntoView({ block: 'nearest' });
        } else {
            listItems[i].classList.remove('selected');
        }
    }
}

// Fetch Suggestions from Backend API
async function fetchSuggestions(prefix) {
    try {
        // Show loading state
        suggestionsList.innerHTML = '<li class="no-suggestions-item"><i class="fa-solid fa-spinner fa-spin"></i> Loading suggestions...</li>';
        openDropdown();
        
        const response = await fetch(`/suggest?q=${encodeURIComponent(prefix)}&mode=${currentMode}`);
        if (!response.ok) throw new Error('API request failed');
        
        currentSuggestions = await response.json();
        selectedSuggestionIndex = -1;
        
        renderSuggestions(prefix);
    } catch (error) {
        console.error('Error fetching suggestions:', error);
        suggestionsList.innerHTML = '<li class="no-suggestions-item text-red"><i class="fa-solid fa-circle-exclamation text-red"></i> Failed to load suggestions</li>';
        openDropdown();
    }
}

// Fetch Routing Hashing Debug Info
async function fetchRoutingDebug(prefix) {
    try {
        const response = await fetch(`/cache/debug?prefix=${encodeURIComponent(prefix)}`);
        if (!response.ok) throw new Error('Debug endpoint failed');
        
        const debugData = await response.json();
        
        // Update dashboard panel
        debugPrefix.textContent = `"${debugData.prefix}"`;
        debugHash.textContent = debugData.prefix_hash;
        debugNode.textContent = debugData.mapped_node;
        
        // Highlight routed node card in CSS
        document.querySelectorAll('.node-card').forEach(card => card.classList.remove('routed'));
        const activeCard = document.getElementById(`node-card-${debugData.mapped_node}`);
        if (activeCard) {
            activeCard.classList.add('routed');
        }
    } catch (error) {
        console.error('Error fetching routing debug:', error);
    }
}

// Render Suggestion Items in dropdown
function renderSuggestions(prefix) {
    suggestionsList.innerHTML = '';
    
    if (currentSuggestions.length === 0) {
        const li = document.createElement('li');
        li.className = 'no-suggestions-item';
        li.textContent = 'No matching suggestions found';
        suggestionsList.appendChild(li);
        openDropdown();
        return;
    }
    
    currentSuggestions.forEach((item, index) => {
        const li = document.createElement('li');
        li.className = 'suggestion-item';
        li.addEventListener('click', () => {
            searchInput.value = item.query;
            closeDropdown();
            submitSearch(item.query);
        });
        
        // Bold the matched prefix text
        const queryText = item.query;
        let highlightedHtml = queryText;
        if (queryText.startsWith(prefix.toLowerCase())) {
            const matchedPart = queryText.substring(0, prefix.length);
            const remainingPart = queryText.substring(prefix.length);
            highlightedHtml = `<span class="prefix-match">${escapeHtml(matchedPart)}</span>${escapeHtml(remainingPart)}`;
        } else {
            highlightedHtml = escapeHtml(queryText);
        }
        
        // Formatted timestamp
        const timeDisplay = formatTimeAgo(item.last_searched_at);
        
        li.innerHTML = `
            <div class="suggestion-content">
                <i class="fa-solid fa-clock-rotate-left suggestion-icon"></i>
                <span class="suggestion-text">${highlightedHtml}</span>
            </div>
            <div class="suggestion-metrics">
                <span class="suggestion-count">${item.count.toLocaleString()} searches</span>
                <span class="suggestion-time">${timeDisplay}</span>
            </div>
        `;
        suggestionsList.appendChild(li);
    });
    
    openDropdown();
}

// Trigger Search Submit Event
async function submitSearch(query) {
    const cleanQuery = query.trim();
    if (cleanQuery.length < 3) {
        showToast('Search query must be at least 3 characters long', 'error');
        return;
    }
    
    closeDropdown();
    
    try {
        const response = await fetch('/search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query: cleanQuery })
        });
        
        if (!response.ok) throw new Error('Search failed');
        
        const data = await response.json();
        
        // Show success panel
        resultsPanel.classList.remove('hidden');
        document.getElementById('results-title').textContent = `Searched: "${cleanQuery}"`;
        
        // Instantly flash metrics update
        fetchMetrics();
        showToast(`Search for "${cleanQuery}" registered!`);
    } catch (error) {
        console.error('Error submitting search:', error);
        showToast('Failed to register search query', 'error');
    }
}

// Fetch System Metrics from API
async function fetchMetrics() {
    try {
        const response = await fetch('/metrics');
        if (!response.ok) throw new Error('Metrics API failed');
        
        const data = await response.json();
        
        // Update connection dot
        const statusDot = document.getElementById('metrics-status-dot');
        statusDot.className = 'status-badge connected';
        statusDot.textContent = 'Live connected';
        
        // Update metric values in HTML
        metricHitRate.textContent = `${data.cache_hit_rate_pct}%`;
        metricHitRateBar.style.width = `${data.cache_hit_rate_pct}%`;
        metricHits.textContent = data.cache_hits;
        metricMisses.textContent = data.cache_misses;
        
        metricLatency.textContent = `${data.p95_response_time_ms.toFixed(2)} ms`;
        metricAvgLatency.textContent = data.avg_response_time_ms.toFixed(2);
        
        metricBufferSize.textContent = `${data.batch_buffer_size} / 50`;
        // Handle buffer badge color based on capacity
        if (data.batch_buffer_size >= 40) {
            metricBufferSize.className = 'buffer-badge text-red';
        } else if (data.batch_buffer_size >= 25) {
            metricBufferSize.className = 'buffer-badge text-yellow';
        } else {
            metricBufferSize.className = 'buffer-badge';
        }
        
        // In periodic countdown (10s interval)
        const secondsLeft = 10 - data.seconds_since_last_flush;
        metricFlushCountdown.textContent = `${Math.max(0, secondsLeft)}s`;
        metricFlushCount.textContent = data.batch_flush_count;
        
        metricDbReads.textContent = data.db_reads;
        metricDbWrites.textContent = data.db_writes;
        
        // Writes saved calculation: Total searches submitted (accumulated writes) - flushes to DB
        // Wait, every flush represents 1 DB transaction block write.
        // So write savings transactions = total buffered inserts - flush batches.
        const saved = Math.max(0, data.db_writes - data.batch_flush_count);
        metricWriteSaved.textContent = saved;
        
        // Update Redis node keys badges
        if (data.redis_node_keys) {
            Object.keys(data.redis_node_keys).forEach(node => {
                const badge = document.getElementById(`node-keys-${node}`);
                if (badge) {
                    badge.textContent = data.redis_node_keys[node];
                }
            });
        }
    } catch (error) {
        console.error('Error fetching metrics:', error);
        
        // Update connection dot to disconnected
        const statusDot = document.getElementById('metrics-status-dot');
        statusDot.className = 'status-badge disconnected';
        statusDot.textContent = 'Offline';
    }
}

// Fetch dynamic trending searches from API
async function fetchTrendingTags() {
    try {
        const response = await fetch('/trending?limit=7');
        if (!response.ok) throw new Error('Failed to fetch trending keywords');
        
        const keywords = await response.json();
        
        trendingTags.innerHTML = '';
        if (keywords.length === 0) {
            trendingTags.innerHTML = '<span class="trending-loading">No trending queries recorded yet</span>';
            return;
        }
        
        keywords.forEach(keyword => {
            const span = document.createElement('span');
            span.className = 'trending-tag';
            span.innerHTML = `<i class="fa-solid fa-arrow-trend-up"></i> ${escapeHtml(keyword)}`;
            span.addEventListener('click', () => {
                searchInput.value = keyword;
                searchClearBtn.classList.remove('hidden');
                fetchSuggestions(keyword);
                fetchRoutingDebug(keyword);
            });
            trendingTags.appendChild(span);
        });
    } catch (error) {
        console.error('Error fetching trending tags:', error);
        trendingTags.innerHTML = '<span class="trending-loading text-red"><i class="fa-solid fa-triangle-exclamation"></i> Error loading trending searches</span>';
    }
}

// Helpers
function openDropdown() {
    suggestionsDropdown.classList.remove('hidden');
}

function closeDropdown() {
    suggestionsDropdown.classList.add('hidden');
}

function clearSearch() {
    searchInput.value = '';
    searchClearBtn.classList.add('hidden');
    closeDropdown();
    resetDebugInfo();
}

function resetDebugInfo() {
    debugPrefix.textContent = '-';
    debugHash.textContent = '-';
    debugNode.textContent = '-';
    document.querySelectorAll('.node-cluster .node-card').forEach(card => card.classList.remove('routed'));
}

// HTML Escaper
function escapeHtml(text) {
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    };
    return text.replace(/[&<>"']/g, function(m) { return map[m]; });
}

// Toast notification helper
function showToast(message, type = 'success') {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    
    if (type === 'error') {
        toast.style.borderColor = 'var(--accent-red)';
        toast.innerHTML = `<i class="fa-solid fa-circle-exclamation text-red"></i> ${message}`;
    } else {
        toast.style.borderColor = 'var(--accent-purple)';
        toast.innerHTML = `<i class="fa-solid fa-circle-check text-green"></i> ${message}`;
    }
    
    toast.classList.remove('hidden');
    
    // Hide toast after 3 seconds
    setTimeout(() => {
        toast.classList.add('hidden');
    }, 3000);
}

// Formats a SQL timestamp string to human-friendly age string
function formatTimeAgo(timestampStr) {
    try {
        const date = new Date(timestampStr.replace(/-/g, '/')); // Compatibility replacement
        const now = new Date();
        const diffMs = now - date;
        const diffMins = Math.floor(diffMs / 60000);
        const diffHours = Math.floor(diffMs / 3600000);
        const diffDays = Math.floor(diffMs / 86400000);
        
        if (diffMins < 1) return 'just now';
        if (diffMins < 60) return `${diffMins}m ago`;
        if (diffHours < 24) return `${diffHours}h ago`;
        if (diffDays === 1) return 'yesterday';
        return `${diffDays}d ago`;
    } catch (e) {
        return 'recently';
    }
}
