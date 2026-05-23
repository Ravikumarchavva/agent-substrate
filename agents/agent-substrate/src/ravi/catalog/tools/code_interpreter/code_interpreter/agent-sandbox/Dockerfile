FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
	MPLBACKEND=Agg \
	MPLCONFIGDIR=/tmp/matplotlib

RUN pip install --no-cache-dir \
	fastapi \
	"uvicorn[standard]" \
	python-multipart \
	httpx \
	# --- data science core --- \
	numpy \
	pandas \
	scipy \
	scikit-learn \
	# --- visualisation --- \
	matplotlib \
	seaborn \
	plotly \
	kaleido \
	# --- file format support --- \
	openpyxl \
	xlrd \
	pyarrow \
	polars \
	pdfplumber \
	Pillow \
	# --- requests kept for compat --- \
	requests

# Create default workspace directory
RUN mkdir -p /app/workspace /tmp/matplotlib

# Build with the repository root as context so this path resolves correctly:
# docker build -f src/ravi/catalog/tools/code_interpreter/code_interpreter/agent-sandbox/Dockerfile -t code-interpreter:latest .
COPY src/ravi/catalog/tools/code_interpreter/code_interpreter/sandbox_runtime.py /app/sandbox_runtime.py

EXPOSE 8888

CMD ["uvicorn", "sandbox_runtime:app", "--host", "0.0.0.0", "--port", "8888"]
