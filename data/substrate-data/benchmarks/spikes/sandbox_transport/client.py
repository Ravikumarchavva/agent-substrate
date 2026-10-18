"""Runs INSIDE nsjail: no network namespace egress, only the bind-mounted socket + token file."""

import statistics
import time

import polars as pl
import pyarrow.flight as fl

results = {}
try:
    import socket

    socket.create_connection(("1.1.1.1", 443), timeout=3)
    results["internet blocked"] = False
except OSError:
    results["internet blocked"] = True

token = open("/run/substrate-data/token").read().strip()
client = fl.connect("grpc+unix:///run/substrate-data/flight.sock")
auth = fl.FlightCallOptions(headers=[(b"authorization", f"Bearer {token}".encode())])

try:
    client.do_get(
        fl.Ticket(b"small"), fl.FlightCallOptions(headers=[(b"authorization", b"Bearer nope")])
    ).read_all()
    results["bad token rejected"] = False
except fl.FlightUnauthenticatedError:
    results["bad token rejected"] = True

df = pl.from_arrow(client.do_get(fl.Ticket(b"small"), auth).read_all())
results["polars result"] = df.to_dicts() == [{"answer": 42}]

lat = []
for _ in range(200):  # connection reuse: same channel, many small calls
    t = time.perf_counter()
    client.do_get(fl.Ticket(b"small"), auth).read_all()
    lat.append((time.perf_counter() - t) * 1000)
results["small DoGet p50 ms"] = round(statistics.median(lat), 3)
results["small DoGet p99 ms"] = round(sorted(lat)[int(len(lat) * 0.99) - 1], 3)

t = time.perf_counter()
total = 0
reader = client.do_get(
    fl.Ticket(b"big:1536"), auth
)  # 1.5 GiB streamed under a 1 GiB jail memory cap
for chunk in reader:
    total += chunk.data.nbytes
secs = time.perf_counter() - t
results["streamed GiB"] = round(total / 2**30, 2)
results["stream GiB/s"] = round(total / 2**30 / secs, 2)

reader = client.do_get(fl.Ticket(b"endless"), auth)
for i, _ in enumerate(reader):
    if i == 20:
        reader.cancel()
        break
results["cancel issued"] = True

try:
    client.do_get(
        fl.Ticket(b"slow"), fl.FlightCallOptions(timeout=1, headers=auth.headers)
    ).read_all()
    results["timeout enforced"] = False
except fl.FlightTimedOutError:
    results["timeout enforced"] = True

for k, v in results.items():
    print(f"CLIENT {k}: {v}")
