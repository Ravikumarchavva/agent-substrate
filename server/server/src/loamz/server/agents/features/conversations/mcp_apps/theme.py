"""The theme bridge injected into every bundled MCP app page: colour variables and the ui/initialize handshake."""

from __future__ import annotations

_MCP_APP_THEME_STYLE = """
<style id="loamz-mcp-theme-vars">
:root {
    color-scheme: dark;
    --mcp-bg: #171717;
    --mcp-surface: #1f1f1f;
    --mcp-surface-muted: #262626;
    --mcp-surface-strong: #111111;
    --mcp-surface-elevated: #0b0b0d;
    --mcp-border: #2a2a2a;
    --mcp-border-strong: #3f3f46;
    --mcp-text: #f5f5f5;
    --mcp-text-muted: #a1a1aa;
    --mcp-text-soft: #71717a;
    --mcp-shadow: rgba(0, 0, 0, 0.24);
    --mcp-overlay: rgba(0, 0, 0, 0.5);
    --mcp-code-bg: #18181b;
    --mcp-highlight: rgba(37, 99, 235, 0.14);
    --mcp-accent: #2563eb;
    --mcp-accent-soft: rgba(37, 99, 235, 0.14);
    --mcp-success: #22c55e;
    --mcp-success-soft: rgba(34, 197, 94, 0.12);
    --mcp-warning: #f59e0b;
    --mcp-warning-soft: rgba(245, 158, 11, 0.14);
    --mcp-danger: #ef4444;
    --mcp-danger-soft: rgba(239, 68, 68, 0.14);
}

html[data-theme="light"] {
    color-scheme: light;
    --mcp-bg: #f7f4ee;
    --mcp-surface: #ffffff;
    --mcp-surface-muted: #f2ede3;
    --mcp-surface-strong: #ebe4d8;
    --mcp-surface-elevated: #fcfaf6;
    --mcp-border: #e2d9ca;
    --mcp-border-strong: #cfc3b0;
    --mcp-text: #171717;
    --mcp-text-muted: #5f5b53;
    --mcp-text-soft: #8b857a;
    --mcp-shadow: rgba(15, 23, 42, 0.08);
    --mcp-overlay: rgba(15, 23, 42, 0.18);
    --mcp-code-bg: #f5efe5;
    --mcp-highlight: rgba(37, 99, 235, 0.1);
    --mcp-accent: #2563eb;
    --mcp-accent-soft: rgba(37, 99, 235, 0.1);
    --mcp-success: #16a34a;
    --mcp-success-soft: rgba(22, 163, 74, 0.1);
    --mcp-warning: #d97706;
    --mcp-warning-soft: rgba(217, 119, 6, 0.12);
    --mcp-danger: #dc2626;
    --mcp-danger-soft: rgba(220, 38, 38, 0.1);
}

html, body {
    background: var(--mcp-bg);
    color: var(--mcp-text);
}

body {
    accent-color: var(--mcp-accent);
}

button, input, select, textarea {
    font: inherit;
}

::selection {
    background: var(--mcp-highlight);
}
</style>
"""

_MCP_APP_THEME_SCRIPT = """
<script id="loamz-mcp-theme-bridge">
(function () {
    if (window.__LOAMZ_MCP_THEME_BRIDGE__) {
        return;
    }

    const bridge = {
        initRequestId: null,
        applyTheme(theme) {
            const resolved = theme === "light" ? "light" : "dark";
            document.documentElement.setAttribute("data-theme", resolved);
        },
        requestInit() {
            this.initRequestId = "loamz-theme-init-" + Math.random().toString(36).slice(2);
            window.parent.postMessage(
                { jsonrpc: "2.0", id: this.initRequestId, method: "ui/initialize" },
                "*"
            );
        },
    };

    window.__LOAMZ_MCP_THEME_BRIDGE__ = bridge;

    if (window.matchMedia) {
        bridge.applyTheme(
            window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"
        );
    } else {
        bridge.applyTheme("dark");
    }

    window.addEventListener("message", function (event) {
        const msg = event.data;
        if (!msg || typeof msg !== "object" || msg.jsonrpc !== "2.0") {
            return;
        }

        if (msg.id === bridge.initRequestId && msg.result && typeof msg.result === "object") {
            const hostContext = msg.result.hostContext || {};
            if (typeof hostContext.theme === "string") {
                bridge.applyTheme(hostContext.theme);
            }
            return;
        }

        if (msg.method === "ui/notifications/theme-changed" && msg.params) {
            if (typeof msg.params.theme === "string") {
                bridge.applyTheme(msg.params.theme);
            }
        }
    });

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", function () {
            bridge.requestInit();
        }, { once: true });
    } else {
        bridge.requestInit();
    }
})();
</script>
"""


def inject_theme_bridge(html: str) -> str:
    if "loamz-mcp-theme-bridge" in html:
        return html
    injection = f"{_MCP_APP_THEME_STYLE}\n{_MCP_APP_THEME_SCRIPT}"
    if "</head>" in html:
        return html.replace("</head>", f"{injection}\n</head>", 1)
    return f"{injection}\n{html}"
