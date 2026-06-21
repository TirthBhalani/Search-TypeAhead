import hashlib
import bisect

class ConsistentHashRing:
    def __init__(self, nodes=None, virtual_nodes=100):
        """
        nodes: list of physical node identifiers (e.g., ['redis-1', 'redis-2', 'redis-3'])
        virtual_nodes: number of virtual nodes per physical node
        """
        self.virtual_nodes = virtual_nodes
        self.ring = {}            # hash -> physical_node
        self.sorted_keys = []     # sorted list of hashes on the ring
        
        if nodes:
            for node in nodes:
                self.add_node(node)

    def _hash(self, key: str) -> int:
        """MD5 hashing mapping a key to a 32-bit integer."""
        digest = hashlib.md5(key.encode('utf-8')).hexdigest()
        # Take first 8 characters of hex MD5 and convert to 32-bit int
        return int(digest[:8], 16)

    def add_node(self, node: str):
        """Adds a physical node to the ring with its virtual nodes."""
        for i in range(self.virtual_nodes):
            virtual_node_name = f"{node}#{i}"
            val = self._hash(virtual_node_name)
            self.ring[val] = node
            bisect.insort(self.sorted_keys, val)

    def remove_node(self, node: str):
        """Removes a physical node and its virtual nodes from the ring."""
        for i in range(self.virtual_nodes):
            virtual_node_name = f"{node}#{i}"
            val = self._hash(virtual_node_name)
            if val in self.ring:
                del self.ring[val]
                # Finding indices is fast because sorted_keys size is small (e.g. 3 * 100 = 300)
                self.sorted_keys.remove(val)

    def get_node(self, key: str) -> str:
        """Given a prefix key, returns the responsible physical node."""
        if not self.ring:
            return None
            
        val = self._hash(key)
        # Binary search for the first node key >= key hash
        idx = bisect.bisect_right(self.sorted_keys, val)
        
        # Wrap around if key hash is greater than all nodes' hashes on the ring
        if idx == len(self.sorted_keys):
            idx = 0
            
        return self.ring[self.sorted_keys[idx]]

    def get_distribution(self, keys_list) -> dict:
        """Utility for debugging: returns how keys are distributed among nodes."""
        dist = {}
        for key in keys_list:
            node = self.get_node(key)
            dist[node] = dist.get(node, 0) + 1
        return dist
