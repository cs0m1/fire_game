import json
import grpc
from concurrent import futures

import fire_ra_pb2
import fire_ra_pb2_grpc
from map_state import MapState
from brains.ml_brain import MLBrain
from visualizer import Visualizer

PORT = 50051


class BrainServicer(fire_ra_pb2_grpc.FireRaServiceServicer):
    def __init__(self):
        self.map_state  = MapState()
        self.brain      = MLBrain()
        self.visualizer = Visualizer()
        self._counter   = 0

    def SayHello(self, request, context):
        print(f"[Brain] Hello from: {request.teamName}")
        return fire_ra_pb2.HelloReply(message="Python brain ready")

    def CommunicateWithStreams(self, request_iterator, context):
        for msg in request_iterator:
            if msg.operation != "GameState":
                continue

            try:
                json_data = json.loads(msg.extraJson)
            except json.JSONDecodeError as e:
                print(f"[Brain] Bad JSON: {e}")
                yield fire_ra_pb2.CommandMessage(
                    operation="Error",
                    extraJson=json.dumps({"error": str(e), "commands": []}),
                )
                continue

            try:
                self.map_state.update(json_data)
                result      = self.brain.calculate_moves(self.map_state)
                commands    = result.get("commands", [])
                debug       = result.get("debug_visuals", {})

                self._counter += 1
                self.visualizer.render(self.map_state, debug)

                yield fire_ra_pb2.CommandMessage(
                    counter=self._counter,
                    operation="Commands",
                    extraJson=json.dumps({"commands": commands}),
                )

            except Exception as e:
                print(f"[Brain] Runtime error: {e}")
                yield fire_ra_pb2.CommandMessage(
                    operation="Error",
                    extraJson=json.dumps({"error": str(e), "commands": []}),
                )


def main():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    fire_ra_pb2_grpc.add_FireRaServiceServicer_to_server(BrainServicer(), server)
    server.add_insecure_port(f"[::]:{PORT}")
    server.start()
    print(f"[Brain] gRPC server listening on port {PORT}")
    server.wait_for_termination()


if __name__ == "__main__":
    main()
