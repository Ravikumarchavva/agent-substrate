# substrate-data-client

```python
import substrate_data_client as sd

df = sd.sql("SELECT zone, count(*) AS trips FROM main.nyc.yellow GROUP BY 1")  # polars.DataFrame
sd.search("taxi")  # find tables and views you may read
sd.describe("main.nyc.yellow")  # columns, partitioning, row count and a sample
```

Inside a Substrate code-interpreter sandbox the connection is discovered automatically
(`/run/substrate-data`). Elsewhere pass a URL and a bearer token or an OIDC `ClientCredentials`.
Results stream as Arrow; filters and aggregation run next to the data, so ask SQL for the answer rather
than pulling rows into Python.
