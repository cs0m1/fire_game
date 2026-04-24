# Fire AI — Python Brain

---

## How gRPC works (for this game)

Normal HTTP: you ask → server answers → done. One request, one response.

gRPC bidirectional streaming is different:

```
YOU                          GAME SERVER
 |                               |
 |------- SayHello("Prometheus") -->|   (normal request/response, just a handshake)
 |<---------- "Hello Prometheus" ---|
 |                               |
 |======= open ONE stream ========|   (stays open the whole game)
 |                               |
 |--- NOP (counter=1) ---------->|
 |<-- ACK ------------------------|
 |--- Right (unitId=31) -------->|
 |<-- ACK ------------------------|   ACKs come back almost instantly
 |--- NOP (counter=3) ---------->|
 |<-- ACK ------------------------|
 |                               |   ...some time passes (server game tick)...
 |<-- UnitsFromServer ------------|   server pushes this whenever IT wants
 |--- Left (unitId=31) --------->|
 |<-- ACK ------------------------|
 |<-- UnitsFromServer ------------|   another game tick
```

**Key points:**
- You send commands whenever you want (in a Python generator)
- The server sends `UnitsFromServer` on its own schedule (game ticks), not as a reply to your commands
- Both sides are sending at the same time, independently
- The stream stays open until you close it or the game ends
- `ACK` = "I received your command and queued it" — NOT "I executed it"

### How the Python code does it

```python
def my_commands():
    counter = 0
    while True:
        counter += 1
        yield CommandMessage(teamName="Prometheus", counter=counter,
                             unitId=31, operation="Right")
        time.sleep(0.25)   # control how fast you send

# This loop receives messages FROM the server
# while my_commands() sends messages TO the server, simultaneously
for server_msg in stub.CommunicateWithStreams(my_commands()):
    if server_msg.operation == "UnitsFromServer":
        # this is where you learn where your units are
        units = json.loads(server_msg.extraJson)
```

The generator (`my_commands`) runs in a background thread automatically.
Your `for` loop on the outside receives whatever the server sends back.

### Why counter matters

The server uses `counter` to detect duplicate or out-of-order commands.
Always increment it. Never reuse a number in the same session.

---

## How to run

```bash
source ../myenv/bin/activate

python explore.py        # main AI — open http://localhost:5000 to watch
python snoop_server.py   # just print everything the server sends
python test_map.py       # save one snapshot to latest_map.json and print it
```

---

## How the protocol works

Everything goes over a single gRPC **bidirectional stream** (`CommunicateWithStreams`).
Both sides send and receive the same message type:

```
CommandMessage {
  teamName  string   — your team name
  counter   int32    — increment each message you send
  unitId    uint32   — which unit this command is for (0 = no specific unit)
  operation string   — what you're doing (see tables below)
  extraJson string   — JSON payload, depends on operation
}
```

### What YOU send to the server

| operation | unitId | extraJson | effect |
|-----------|--------|-----------|--------|
| `NOP`     | 0      | ""        | do nothing, keeps the stream alive |
| `Up`      | `<id>` | ""        | move that unit up one cell |
| `Down`    | `<id>` | ""        | move that unit down one cell |
| `Left`    | `<id>` | ""        | move that unit left one cell |
| `Right`   | `<id>` | ""        | move that unit right one cell |

### What the SERVER sends back

| operation           | extraJson contents |
|---------------------|--------------------|
| `ACK`               | `{"message": "ENQUEUED command #N from ..."}` |
| `UnitsFromServer`   | array of unit objects (see format below) |
| `InformationFromServer` | unknown — log it if you see it |

You get an `ACK` for every command you send.
`UnitsFromServer` arrives on the server's game tick (independent of your commands).

---

## Confirmed data format (from real captures)

`UnitsFromServer` extraJson is a **JSON array** of unit objects:

```json
[
  {
    "Id": 31,
    "UnitType": "fireFighter",
    "Owner": "Prometheus",
    "Position": { "IsEmpty": false, "X": 0, "Y": 1 },
    "SeenWaters": [ { "IsEmpty": true, "X": 0, "Y": 0 } ],
    "SeenFires":  [],
    "CurrentWaterLevel": 100,
    "CurrentHP": 1000
  }
]
```

### Unit types (exact server casing)

| UnitType       | HP   | Water       | Stated sight | Stated speed |
|----------------|------|-------------|--------------|--------------|
| `fireFighter`  | 1000 | 100 (inf?)  | 2 cells      | 50           |
| `Firetruck`    | 1500 | 20 (limited)| 8 cells      | 100          |
| `Firecopter`   | 2000 | 5 (limited) | 16 cells     | 200          |

> The server uses inconsistent casing. Always `.lower()` before comparing.

### The `IsEmpty` field

Every coordinate (`Position`, `SeenFires[n]`, `SeenWaters[n]`) has `IsEmpty`.
- `Position.IsEmpty = false` → valid position
- `SeenWaters[n].IsEmpty = true` with valid X/Y → **meaning unknown, needs investigation**

### Known unknowns — measure these, don't guess

- [ ] Exact sight radius per unit type (stated: 2/8/16 — not verified)
- [ ] Exact movement speed (how many cells per tick)
- [ ] Whether `IsEmpty: true` on a seen coordinate means "null" or "depleted"
- [ ] Whether `SeenFires`/`SeenWaters` lists only nearby cells or all known ones
- [ ] Map boundaries (coordinates seen so far: X from -3 to 189)
- [ ] Whether other teams' units appear in the list (haven't seen any yet)
- [ ] What `InformationFromServer` contains

---

## File guide

| file | what it does |
|------|-------------|
| `explore.py`     | **main entry point** — connects, runs AI, serves web UI |
| `map_tracker.py` | tracks cell state (fire/water/obstacle/empty) |
| `explorer_ai.py` | decides where each unit should move |
| `web_viz.py`     | Flask server + canvas UI at localhost:5000 |
| `raw_logger.py`  | writes every server message to a `.jsonl` file |
| `snoop_server.py`| dev tool — dumps raw server output to terminal |
| `test_map.py`    | dev tool — saves one unit snapshot to `latest_map.json` |
| `fire_ra.proto`  | protocol definition (read-only reference) |
| `fire_ra_pb2*.py`| generated from proto — do not edit |
| `latest_map.json`| most recent unit snapshot from `test_map.py` |

---

## Minimal example — send a single command

```python
import grpc, fire_ra_pb2, fire_ra_pb2_grpc

channel = grpc.insecure_channel("10.4.4.59:5001")
stub    = fire_ra_pb2_grpc.FireRaServiceStub(channel)
stub.SayHello(fire_ra_pb2.HelloRequest(teamName="Prometheus"))

def commands():
    yield fire_ra_pb2.CommandMessage(
        teamName="Prometheus", counter=1, unitId=31, operation="Right", extraJson=""
    )

for response in stub.CommunicateWithStreams(commands()):
    print(response.operation, response.extraJson)
```
