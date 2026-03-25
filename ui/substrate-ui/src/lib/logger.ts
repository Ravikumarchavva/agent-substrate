type LogLevel = "debug" | "info" | "warn" | "error";

interface LogContext {
  component?: string;
  action?: string;
  metadata?: Record<string, unknown>;
}

interface LogEntry {
  timestamp: string;
  level: LogLevel;
  message: string;
  component?: string;
  action?: string;
  metadata?: Record<string, unknown>;
  url?: string;
  userAgent?: string;
}

const styles: Record<LogLevel, string> = {
  debug: "color: gray",
  info: "color: blue",
  warn: "color: orange",
  error: "color: red; font-weight: bold",
};

class Logger {
  private buffer: LogEntry[] = [];
  private flushTimer: ReturnType<typeof setTimeout> | null = null;
  private readonly FLUSH_INTERVAL = 5000;
  private readonly MAX_BUFFER = 50;

  debug(message: string, ctx?: LogContext): void {
    this.log("debug", message, ctx);
  }

  info(message: string, ctx?: LogContext): void {
    this.log("info", message, ctx);
  }

  warn(message: string, ctx?: LogContext): void {
    this.log("warn", message, ctx);
  }

  error(message: string, ctx?: LogContext): void {
    this.log("error", message, ctx);
  }

  async flush(): Promise<void> {
    if (this.buffer.length === 0) return;
    if (this.flushTimer) {
      clearTimeout(this.flushTimer);
      this.flushTimer = null;
    }
    const entries = [...this.buffer];
    this.buffer = [];
    try {
      await fetch("/api/logs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entries }),
      });
    } catch {
      this.buffer = [...entries.slice(-25), ...this.buffer].slice(
        0,
        this.MAX_BUFFER,
      );
    }
  }

  private log(level: LogLevel, message: string, ctx?: LogContext): void {
    const entry: LogEntry = {
      timestamp: new Date().toISOString(),
      level,
      message,
      ...ctx,
      url:
        typeof window !== "undefined" ? window.location.pathname : undefined,
      userAgent:
        typeof window !== "undefined" ? navigator.userAgent : undefined,
    };

    const tag = `[${level.toUpperCase()}]`;
    const comp = entry.component ? ` [${entry.component}]` : "";
    console.log(`%c${tag}${comp} ${message}`, styles[level], ctx?.metadata || "");

    if (level === "error" || level === "warn") {
      this.buffer.push(entry);
      if (this.buffer.length >= this.MAX_BUFFER) {
        void this.flush();
      } else {
        this.scheduleFlush();
      }
    }
  }

  private scheduleFlush(): void {
    if (this.flushTimer) return;
    this.flushTimer = setTimeout(() => {
      void this.flush();
    }, this.FLUSH_INTERVAL);
  }
}

export const logger = new Logger();
