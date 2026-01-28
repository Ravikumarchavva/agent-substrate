import logging
import sys
from typing import Any, Dict, Optional, ContextManager
from contextlib import contextmanager

from opentelemetry import trace, metrics
from opentelemetry.trace import Status, StatusCode
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor, BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from agent_framework.logger import setup_logging, logger
from agent_framework.logger import setup_logging, logger


# Initialize logging immediately
setup_logging()
logger = logging.getLogger("agent_framework")

# --- Tracing ---

# Setup default provider (won't output anywhere unless configured)
# The user of this lib should configure the provider (e.g. in the demo script).
# But for convenience we provide a helper.

def configure_opentelemetry(
    service_name: str = "agent-framework", 
    otlp_trace_endpoint: Optional[str] = None,
    otlp_metric_endpoint: Optional[str] = None
):
    """
    Configure OpenTelemetry. 
    If endpoints are provided, use OTLP Exporters.
    Otherwise, use Console Exporter for demo purposes.
    """
    resource = Resource.create({"service.name": service_name})
    
    # --- Trace Provider ---
    tracer_provider = TracerProvider(resource=resource)
    
    if otlp_trace_endpoint:
        # Using HTTP OTLP exporter as it's generally more reliable for local setups
        # Ensure endpoint is a full URL
        endpoint = otlp_trace_endpoint
        if "://" not in endpoint:
            endpoint = f"http://{endpoint}"
        if not endpoint.endswith("/v1/traces"):
            endpoint = endpoint.rstrip("/") + "/v1/traces"
            
        span_exporter = OTLPSpanExporter(endpoint=endpoint)
        span_processor = BatchSpanProcessor(span_exporter)
    else:
        span_processor = SimpleSpanProcessor(ConsoleSpanExporter())
        
    tracer_provider.add_span_processor(span_processor)
    trace.set_tracer_provider(tracer_provider)
    
    # --- Meter Provider ---
    if otlp_metric_endpoint:
        # Metrics often still use gRPC in OTLP
        grpc_endpoint = otlp_metric_endpoint
        if "://" in grpc_endpoint:
            grpc_endpoint = grpc_endpoint.split("://")[-1]
        reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=grpc_endpoint, insecure=True))
    else:
        reader = PeriodicExportingMetricReader(ConsoleMetricExporter())
        
    meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(meter_provider)

class Tracer:
    """Wrapper around OpenTelemetry Tracer."""
    
    def __init__(self, name: str = "agent_framework"):
        self._tracer = trace.get_tracer(name)

    @contextmanager
    def start_span(self, name: str, attributes: Dict[str, Any] = None) -> ContextManager[trace.Span]:
        with self._tracer.start_as_current_span(name, attributes=attributes or {}) as span:
            yield span

# --- Metrics ---

class Metrics:
    """Wrapper around OpenTelemetry Metrics."""
    
    def __init__(self, name: str = "agent_framework"):
        self._meter = metrics.get_meter(name)
        self._counters = {}
        self._histograms = {}

    def increment_counter(self, name: str, value: int = 1, tags: Dict[str, str] = None):
        if name not in self._counters:
            self._counters[name] = self._meter.create_counter(name)
        self._counters[name].add(value, attributes=tags)

    def record_histogram(self, name: str, value: float, tags: Dict[str, str] = None):
        if name not in self._histograms:
            self._histograms[name] = self._meter.create_histogram(name)
        self._histograms[name].record(value, attributes=tags)

# Global instances
global_tracer = Tracer()
global_metrics = Metrics()
