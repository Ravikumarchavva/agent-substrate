"""Host-side Flight server on a Unix socket (stands in for the substrate-data Flight endpoint)."""

import sys
import time

import pyarrow as pa
import pyarrow.flight as fl

TOKEN = open("/run/host-secrets/token").read().strip()


class Auth(fl.ServerMiddlewareFactory):
    def start_call(self, info, headers):
        if headers.get("authorization", [""])[0] != "Bearer " + TOKEN:
            raise fl.FlightUnauthenticatedError("bad token")


class Server(fl.FlightServerBase):
    def do_get(self, context, ticket):
        kind, _, arg = ticket.ticket.decode().partition(":")
        if kind == "small":
            return fl.RecordBatchStream(pa.table({"answer": [42]}))
        if kind == "big":  # arg = total MiB, streamed batch by batch, never materialized
            rows = 1_000_000
            batch = pa.record_batch({f"c{i}": pa.array(range(rows), pa.int64()) for i in range(4)})
            n = int(arg) * 1024 * 1024 // batch.nbytes + 1
            return fl.GeneratorStream(batch.schema, (batch for _ in range(n)))
        if kind == "endless":  # cancellation probe: logs when the client cancels
            batch = pa.record_batch({"v": pa.array(range(100_000), pa.int64())})

            def gen():
                sent = 0
                while True:
                    if context.is_cancelled():
                        print(f"SERVER saw cancellation after {sent} batches", flush=True)
                        return
                    sent += 1
                    yield batch

            return fl.GeneratorStream(batch.schema, gen())
        if kind == "slow":
            time.sleep(5)
            return fl.RecordBatchStream(pa.table({"v": [1]}))
        raise fl.FlightServerError("unknown ticket")


Server(location=fl.Location.for_grpc_unix(sys.argv[1]), middleware={"auth": Auth()}).serve()
