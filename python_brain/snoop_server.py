"""
snoop_server.py — dev tool: move the Firecopter and log everything.

- Moves the Firecopter in one direction until it stops moving (blocked)
- When blocked: logs it and tries the next direction
- Prints every UnitsFromServer message so we can see the real data format
- Also logs all other message types so we don't miss anything

Run:  python snoop_server.py
"""
import grpc
import json
import time
import fire_ra_pb2
import fire_ra_pb2_grpc

SERVER = "10.4.4.59:5001"
TEAM   = "Prometheus"

DIRECTIONS = ["Right", "Down", "Left", "Up"]  # cycle through these when blocked

# ── shared state between the sender and receiver ──────────────────────────────
_drone_id       = None   # filled in once we get UnitsFromServer
_drone_pos      = None   # (x, y) from last tick
_current_dir_i  = 0      # index into DIRECTIONS
_last_sent_dir  = None   # direction we just sent (to detect if it worked)
_last_sent_pos  = None   # position when we sent that command


def _pick_direction():
    return DIRECTIONS[_current_dir_i % len(DIRECTIONS)]


def command_generator():
    global _current_dir_i, _last_sent_dir, _last_sent_pos
    counter = 0

    while True:
        counter += 1

        if _drone_id is None:
            # don't know our unit id yet — send NOP and wait
            yield fire_ra_pb2.CommandMessage(
                teamName=TEAM, counter=counter, unitId=0,
                operation="NOP", extraJson=""
            )
            time.sleep(0.3)
            continue

        direction = _pick_direction()
        _last_sent_dir = direction
        _last_sent_pos = _drone_pos  # snapshot of position before this command

        yield fire_ra_pb2.CommandMessage(
            teamName=TEAM, counter=counter,
            unitId=_drone_id, operation=direction, extraJson=""
        )
        time.sleep(0.25)


def main():
    global _drone_id, _drone_pos, _current_dir_i

    print(f"Connecting to {SERVER} as '{TEAM}' ...")
    channel = grpc.insecure_channel(SERVER)
    stub    = fire_ra_pb2_grpc.FireRaServiceStub(channel)

    try:
        r = stub.SayHello(fire_ra_pb2.HelloRequest(teamName=TEAM))
        print(f"SayHello: {r.message}\n")
    except grpc.RpcError as e:
        print(f"SayHello failed: {e.details()}\n")

    print(f"Moving Firecopter — logging all server messages\n{'─'*60}")

    try:
        for msg in stub.CommunicateWithStreams(command_generator()):
            op = msg.operation

            # ── ACK: just print the counter so we know commands are going through
            if op == "ACK":
                print(f"  ACK #{msg.counter}")
                continue

            # ── anything unexpected: print it fully so we learn about it
            if op != "UnitsFromServer":
                print(f"\n*** UNKNOWN OP: {op!r} ***")
                if msg.extraJson:
                    print(json.dumps(json.loads(msg.extraJson), indent=2))
                continue

            # ── UnitsFromServer: the interesting one ──────────────────────────
            try:
                units = json.loads(msg.extraJson)
            except json.JSONDecodeError as e:
                print(f"JSON parse error: {e}  raw: {msg.extraJson[:200]}")
                continue

            print(f"\n{'═'*60}")
            print(f"UnitsFromServer — {len(units)} unit(s)")

            for u in units:
                uid   = u["Id"]
                utype = u["UnitType"]
                owner = u.get("Owner", "?")
                pos   = u["Position"]
                x, y  = pos["X"], pos["Y"]
                fires  = u.get("SeenFires", [])
                waters = u.get("SeenWaters", [])
                hp     = u.get("CurrentHP", "?")
                water  = u.get("CurrentWaterLevel", "?")

                print(f"  [{utype}] id={uid} owner={owner}  pos=({x},{y})  "
                      f"HP={hp}  water={water}")
                if fires:
                    print(f"    SeenFires:  {fires}")
                if waters:
                    print(f"    SeenWaters: {waters}")

                # track drone position + detect blocked movement
                if utype.lower() == "firecopter" and owner == TEAM:
                    prev_id  = _drone_id
                    _drone_id = uid

                    if _drone_pos is not None and _last_sent_dir is not None:
                        moved = (x, y) != _last_sent_pos
                        if moved:
                            dx = x - _last_sent_pos[0]
                            dy = y - _last_sent_pos[1]
                            print(f"    ✓ MOVED {_last_sent_dir}  ({_last_sent_pos[0]},{_last_sent_pos[1]}) → ({x},{y})  delta=({dx},{dy})")
                        else:
                            print(f"    ✗ BLOCKED going {_last_sent_dir} at ({x},{y})  → trying next direction")
                            _current_dir_i += 1

                    _drone_pos = (x, y)

    except grpc.RpcError as e:
        print(f"\nStream ended: {e.details()}")
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
