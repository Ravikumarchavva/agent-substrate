# loamz-data-reader

Loamz Data's document reader: reads an upload (PDF, Office, images) into an `ExtractionResult`
(`loamz.data.storage.documents`) over HTTP, with layout-aware OCR (PaddleOCR or PaddleOCR-VL), charts and tables as
images, Office conversion and a structural security scan that runs before any parser touches the bytes.

It runs on Python 3.13 (PaddlePaddle publishes no 3.14 wheel yet), so it is outside the uv workspace with its own lock:

```bash
cd data/reader
uv sync --extra test            # add --extra paddle (CPU) or --extra paddle-gpu for the OCR engines
uv run pytest -n 4
uv run uvicorn loamz.data.reader.app:app --port 8080
```

Configuration is `LOAMZ_DATA_READER_*` (`config.py`). The data server reaches it through
`LOAMZ_DATA_DOCUMENT_READER_URL`.
