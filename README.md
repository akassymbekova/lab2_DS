# LAB 2: Logical Clocks and Replication Consistency (3 Nodes)

## System Overview

A replicated key-value store running on 3 distributed nodes (AWS EC2 instances):
- Each node maintains **local state** and a **logical clock**
- Updates propagate to peer nodes with **logical timestamps**
- **Last-Writer-Wins (LWW)** conflict resolution using Lamport timestamps

### System Model
```
┌─────────┐         ┌─────────┐
│ Node A  │←───────→│ Node B  │
│ :8000   │         │ :8001   │
└────┬────┘         └────┬────┘
     │                   │
     └────→┌─────────┐←──┘
           │ Node C  │
           │ :8002   │
           └─────────┘
```

- **Communication**: Message passing over HTTP (JSON payloads)
- **Consistency**: Eventual consistency with LWW
- **Failures**: Message delay/reordering, temporary node outages, retry logic

##  Prerequisites

- [ ] Three AWS EC2 instances created and reachable via SSH
- [ ] Inbound rules configured for ports 8000, 8001, 8002
- [ ] Python 3 installed on each node
- [ ] SSH key pair for accessing instances

## EC2 Setup

### 1. Launch Instances

Create three **Ubuntu 22.04** EC2 instances:

| Instance Name | Node ID | Port |
|---------------|---------|------|
| node-A        | A       | 8000 |
| node-B        | B       | 8001 |
| node-C        | C       | 8002 |

### 2. Security Group Configuration

**Inbound Rules:**

| Type       | Protocol | Port Range | Source          |
|------------|----------|------------|-----------------|
| SSH        | TCP      | 22         | Your IP         |
| Custom TCP | TCP      | 8000-8002  | Security Group  |

### 3. Key Pair

Use the **same key pair** for all three instances for easier management.

##  Installation

### Install Python on Each Node
```bash
sudo apt update && sudo apt install -y python3
```

### Upload Starter Code

Use SCP to upload files to each instance:
```bash
scp -i your-key.pem node.py client.py ubuntu@<PUBLIC-IP>:~/
```

Or use git:
```bash
git clone <your-repo-url>
cd <repo-directory>
```

## Running the System

### Step 1: Find Private IPs

On each instance, get the private IP address:
```bash
ip a | grep inet
```

Look for IPs like `172.31.x.x` or `10.0.x.x`.

### Step 2: Start Nodes

**Important case**: Use **PRIVATE IPs** for peer communication within VPC.

**On Node A:**
```bash
python3 node.py --id A --port 8000 --peers http://<IP-B>:8001,http://<IP-C>:8002
```

**On Node B:**
```bash
python3 node.py --id B --port 8001 --peers http://<IP-A>:8000,http://<IP-C>:8002
```

**On Node C:**
```bash
python3 node.py --id C --port 8002 --peers http://<IP-A>:8000,http://<IP-B>:8001
```

### Step 3: Verify Connectivity

From Node A, test connectivity to peers:
```bash
nc -vz <IP-B> 8001
nc -vz <IP-C> 8002
```

Expected output: `Connection to <IP> 8001 port [tcp/*] succeeded!`

##  Lamport Clock Implementation

### Rules (Required)

1. **Initialize**: `L = 0`
2. **Local Event**: `L = L + 1`
3. **Send Message**: Increment `L`, attach timestamp to message
4. **Receive Message** (with timestamp `t`): `L = max(L, t) + 1`

### Example
```
Node A: L=0
  → Local event: L=1
  → Send to B with timestamp=1
  
Node B: L=0
  ← Receive from A (t=1): L=max(0,1)+1=2
  → Local event: L=3
```

##  Experiments

### Part 1: Replicated Key-Value Store

#### Supported Operations

- **PUT(key, value)**: Store a key-value pair
- **GET(key)**: Retrieve value for a key
- **STATUS**: View node state and clock

#### Implementation Requirements

For each `PUT` request:
1. Update local store
2. Update Lamport clock
3. Replicate to all peers
4. Track Lamport timestamp per key for LWW

### Part 2: Required Causality Scenarios

####  Scenario A: Delay/Reorder

**Goal**: Show ordering effects due to network delay

**Steps**:
1. Add artificial delay from Node A → Node C only
2. Send `PUT x=1` from Node A
3. Observe that Node B applies update before Node C
4. Check `/status` on all nodes

**Expected**: Node B converges faster than Node C

#### Scenario B: Concurrent Writes

**Goal**: Demonstrate LWW conflict resolution

**Steps**:
1. Send `PUT x=1` to Node A (simultaneously)
2. Send `PUT x=2` to Node B (simultaneously)
3. Wait for convergence
4. Check final value on all nodes

**Expected**: All nodes converge to same value (highest timestamp wins)

#### Scenario C: Temporary Outage

**Goal**: Show eventual consistency after node recovery

**Steps**:
1. Stop Node C (`Ctrl+C` or kill process)
2. Perform updates on Nodes A and B
3. Restart Node C
4. Check `/status` for convergence

**Expected**: Node C catches up and converges to same state

## Client Usage

### PUT Operation
```bash
python3 client.py --node http://<IP-A>:8000 put x 1
python3 client.py --node http://<IP-A>:8000 put y hello
```

### GET Operation
```bash
python3 client.py --node http://<IP-B>:8001 get x
python3 client.py --node http://<IP-C>:8002 get y
```

### Check Status
```bash
python3 client.py --node http://<IP-A>:8000 status
```

**Status Output Example:**
```json
{
  "node_id": "A",
  "lamport_clock": 15,
  "store": {
    "x": {"value": 1, "timestamp": 10},
    "y": {"value": "hello", "timestamp": 12}
  },
  "peers": ["http://IP-B:8001", "http://IP-C:8002"]
}
```

##  Implementation Guide

### Code Structure
```
.
├── node.py       # Main server implementation
├── client.py     # CLI client for testing
└── README.md     # This file
```

### Troubleshooting

### Connection Refused

**Problem**: `Connection refused` when starting nodes

**Solutions**:
- Check security group inbound rules
- Verify you're using **private IPs** (not public)
- Ensure ports 8000-8002 are open within VPC

### Port Already in Use

**Problem**: `Address already in use` error

**Solutions**:
```bash
# Find process using port
sudo lsof -i :8000

# Kill the process
kill -9 <PID>
```

