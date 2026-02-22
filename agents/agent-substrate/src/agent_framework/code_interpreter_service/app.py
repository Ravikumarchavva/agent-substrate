"""Standalone FastAPI application for the Code Interpreter service.

Deploy this on a separate k3s pod with privileged access to /dev/kvm.
The main backend calls it via HTTP (see http_client.py).

Usage::

    uvicorn agent_framework.code_interpreter_service.app:app \
        --host 0.0.0.0 --port 8080 --workers 1

NOTE: Must run with ``--workers 1`` because SessionManager uses
in-process asyncio state.  Scaling is done via StatefulSet replicas.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agent_framework.tools.code_interpreter.config import CodeInterpreterConfig
from agent_framework.tools.code_interpreter.session_manager import SessionManager

from .config import ServiceConfig
from .routes import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _fc_config_from(svc: ServiceConfig) -> CodeInterpreterConfig:
    """Map service env-var config to the Firecracker CodeInterpreterConfig."""
    return CodeInterpreterConfig(
        kernel_path=svc.fc_kernel_path,
        base_rootfs_path=svc.fc_rootfs_path,
        vcpu_count=svc.fc_vcpu_count,
        mem_size_mib=svc.fc_mem_size_mib,
        pool_size=svc.pool_size,
        pool_max_size=svc.pool_max_size,
        default_timeout=svc.default_timeout,
        max_timeout=svc.max_timeout,
        max_code_size=svc.max_code_size,
        vsock_port=svc.vsock_port,
        vsock_guest_cid=svc.vsock_guest_cid_start,
        work_dir=svc.work_dir,
        session_timeout=svc.session_timeout,
        max_sessions=svc.max_sessions,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start SessionManager + VMPool on boot, clean up on shutdown."""
    svc_config = ServiceConfig()
    fc_config = _fc_config_from(svc_config)

    logger.info(
        "Starting Code Interpreter service  pod=%s  pool=%d  max_sessions=%d  "
        "kernel=%s  rootfs=%s",
        svc_config.pod_name,
        svc_config.pool_size,
        svc_config.max_sessions,
        svc_config.fc_kernel_path,
        svc_config.fc_rootfs_path,
    )

    sm = SessionManager(config=fc_config)
    await sm.start()

    app.state.session_manager = sm
    app.state.config = svc_config
    app.state.start_time = time.monotonic()

    logger.info(
        "Code Interpreter service ready  pod=%s  warm_vms=%d",
        svc_config.pod_name,
        sm._pool.available,
    )

    yield

    logger.info(
        "Shutting down Code Interpreter service  active_sessions=%d",
        sm.session_count,
    )
    await sm.stop()
    logger.info("Code Interpreter service stopped")


def create_app() -> FastAPI:
    """Build and return the FastAPI application."""
    application = FastAPI(
        title="Code Interpreter Service",
        version="1.0.0",
        description=(
            "Firecracker-based secure code execution service for AI agents. "
            "Each session gets a persistent microVM with Python + bash."
        ),
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(router)
    return application


app = create_app()
