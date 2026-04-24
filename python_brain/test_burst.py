"""
snoop_burst_test.py — test: blast 200 Right commands with NO delay,
then watch how many cells the drone actually moves per tick.

Goal: find out if queuing tons of commands = faster movement,
or if the server caps it at some max per tick.

Run:  python snoop_burst_test.py
"""
import grpc
import json
import time
import fire_ra_pb2
import fire_ra_pb2_grpc

SERVER = "10.4.4.59:5001"
TEAM   = "Prometheus"

BURST_COUNT = 200        # how many Right commands to blast
BURST_DIR   = "Right"    # direction for the burst

# ── state ─────────────────────────────────────────────────────────────────────
_drone_id    = None
_drone_pos   = None
_burst_done  = False
_burst_sent  = 0
_tick_num    = 0
_start_pos   = None      # position when burst started
_positions   = []         # list of (tick, x, y) to analyze at end


def command_generator():
    global _burst_done, _burst_sent
    counter = 0

    while True:
        counter += 1

        # Phase 0: waiting for drone ID
        if _drone_id is None:
            yield fire_ra_pb2.CommandMessage(
                teamName=TEAM, counter=counter, unitId=0,
                operation="NOP", extraJson=""
            )
            time.sleep(0.3)
            continue

        # Phase 1: BURST — send 200 commands as fast as possible (no sleep!)
        if not _burst_done and _burst_sent < BURST_COUNT:
            _burst_sent += 1
            if _burst_sent == 1:
                print(f"\n{'🚀'*20}")
                print(f"BURST START: sending {BURST_COUNT} x {BURST_DIR} with ZERO delay")
                print(f"{'🚀'*20}\n")
            if _burst_sent % 50 == 0:
                print(f"  ... sent {_burst_sent}/{BURST_COUNT}")

            yield fire_ra_pb2.CommandMessage(
                teamName=TEAM, counter=counter,
                unitId=_drone_id, operation=BURST_DIR, extraJson=""
            )
            # NO sleep here — that's the whole point!
            continue

        if not _burst_done and _burst_sent >= BURST_COUNT:
            _burst_done = True
            print(f"\n  ✅ All {BURST_COUNT} commands sent! Now watching movement...\n")

        # Phase 2: POST-BURST — send NOPs slowly, just watch where we end up
        yield fire_ra_pb2.CommandMessage(
            teamName=TEAM, counter=counter, unitId=_drone_id,
            operation="NOP", extraJson=""
        )
        time.sleep(0.5)  # slow poll to watch the drone coast


def main():
    global _drone_id, _drone_pos, _tick_num, _start_pos

    print(f"Connecting to {SERVER} as '{TEAM}' ...")
    channel = grpc.insecure_channel(SERVER)
    stub    = fire_ra_pb2_grpc.FireRaServiceStub(channel)

    try:
        r = stub.SayHello(fire_ra_pb2.HelloRequest(teamName=TEAM))
        print(f"SayHello: {r.message}\n")
    except grpc.RpcError as e:
        print(f"SayHello failed: {e.details()}\n")

    print(f"BURST TEST: will send {BURST_COUNT} x '{BURST_DIR}' with no delay")
    print(f"{'─'*60}")

    no_move_count = 0  # how many ticks with zero movement after burst

    try:
        for msg in stub.CommunicateWithStreams(command_generator()):
            op = msg.operation

            if op == "ACK":
                continue

            if op != "UnitsFromServer":
                print(f"\n*** {op!r}: {msg.extraJson[:200] if msg.extraJson else ''}")
                continue

            try:
                units = json.loads(msg.extraJson)
            except json.JSONDecodeError:
                continue

            for u in units:
                utype = u["UnitType"]
                owner = u.get("Owner", "?")
                if utype.lower() != "firecopter" or owner != TEAM:
                    continue

                uid = u["Id"]
                pos = u["Position"]
                x, y = pos["X"], pos["Y"]

                _drone_id = uid
                _tick_num += 1

                if _drone_pos is None:
                    _drone_pos = (x, y)
                    _start_pos = (x, y)
                    print(f"  tick {_tick_num:3d}: START pos=({x},{y})")
                    _positions.append((_tick_num, x, y, 0, 0))
                    continue

                dx = x - _drone_pos[0]
                dy = y - _drone_pos[1]
                total_dx = x - _start_pos[0]
                total_dy = y - _start_pos[1]
                _positions.append((_tick_num, x, y, dx, dy))

                moved = (dx != 0 or dy != 0)
                marker = "→" if moved else "·"
                print(f"  tick {_tick_num:3d}: pos=({x},{y})  Δ=({dx:+d},{dy:+d})  "
                      f"total_from_start=({total_dx:+d},{total_dy:+d})  {marker}")

                _drone_pos = (x, y)

                # After burst: if drone stops moving for 5 ticks, print summary
                if _burst_done:
                    if not moved:
                        no_move_count += 1
                    else:
                        no_move_count = 0

                    if no_move_count >= 5:
                        _print_summary()
                        return

    except grpc.RpcError as e:
        print(f"\nStream ended: {e.details()}")
    except KeyboardInterrupt:
        print("\nStopped.")

    _print_summary()


def _print_summary():
    if not _positions:
        print("No data collected.")
        return

    print(f"\n{'═'*60}")
    print("BURST TEST RESULTS")
    print(f"{'═'*60}")
    print(f"Burst: {BURST_COUNT} x {BURST_DIR}")
    print(f"Start: ({_start_pos[0]}, {_start_pos[1]})")
    print(f"End:   ({_drone_pos[0]}, {_drone_pos[1]})")
    total_dx = _drone_pos[0] - _start_pos[0]
    total_dy = _drone_pos[1] - _start_pos[1]
    print(f"Total movement: Δx={total_dx}  Δy={total_dy}")
    print(f"Ticks observed: {len(_positions)}")

    # per-tick deltas
    deltas = [(t, dx, dy) for t, x, y, dx, dy in _positions if dx != 0 or dy != 0]
    if deltas:
        moves_per_tick = [abs(dx) + abs(dy) for _, dx, dy in deltas]
        print(f"\nMoving ticks: {len(deltas)}")
        print(f"Cells/tick: min={min(moves_per_tick)} max={max(moves_per_tick)} "
              f"avg={sum(moves_per_tick)/len(moves_per_tick):.1f}")
        print(f"All deltas: {[(dx,dy) for _,dx,dy in deltas]}")
    else:
        print("\nNo movement detected at all!")

    if total_dx == BURST_COUNT or total_dy == BURST_COUNT:
        print(f"\n🔥 MOVED EXACTLY {BURST_COUNT} cells — every command counted!")
    elif abs(total_dx) + abs(total_dy) > 0:
        ratio = (abs(total_dx) + abs(total_dy)) / BURST_COUNT * 100
        print(f"\n📊 Moved {abs(total_dx)+abs(total_dy)} out of {BURST_COUNT} "
              f"requested = {ratio:.0f}% efficiency")
        if ratio < 50:
            print("   → Server likely CAPS commands per tick (not all queued)")
        else:
            print("   → Commands ARE queuing! More commands = faster travel")


if __name__ == "__main__":
    main()