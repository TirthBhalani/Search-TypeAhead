import sys
sys.path.append('backend')
from consistent_hash import ConsistentHashRing

def test_distribution():
    print("Testing Consistent Hashing Ring key distribution...")
    nodes = ["redis-1", "redis-2", "redis-3"]
    ring = ConsistentHashRing(nodes=nodes, virtual_nodes=100)
    
    # Generate 15000 random prefixes (e.g. 'prefix_0', 'prefix_1', etc.)
    prefixes = [f"prefix_{i}" for i in range(15000)]
    
    dist = ring.get_distribution(prefixes)
    
    print("\nResults of Consistent Hashing (15,000 Keys):")
    total_keys = 0
    for node, count in dist.items():
        percentage = (count / len(prefixes)) * 100
        print(f" - {node}: {count} keys ({percentage:.2f}%)")
        total_keys += count
        
    # Verify that all keys are routed
    assert total_keys == len(prefixes), "Some keys were not routed!"
    
    # Verify that no node receives 0 keys (indicates massive skew or mapping error)
    for node in nodes:
        assert dist.get(node, 0) > 0, f"Node {node} received 0 keys!"
        
    print("\nConsistent Hashing Ring test passed successfully! Uniform distribution validated.")

if __name__ == "__main__":
    test_distribution()
