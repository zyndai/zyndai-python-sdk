"""
Prometheus metrics for AG-UI streaming.

Tracks:
- agui_events_emitted_total — Total events emitted (counter)
- agui_active_streams — Current number of open streams (gauge)
- agui_stream_duration_seconds — Time streams remain open (histogram)
"""

import time
import logging
from typing import Optional, Dict
from dataclasses import dataclass, field

logger = logging.getLogger("AGUIMetrics")


@dataclass
class StreamMetrics:
    """In-memory metrics collection for AG-UI streams."""

    events_emitted_total: int = 0
    active_streams_count: int = 0
    stream_durations: list = field(default_factory=list)

    def increment_events(self, count: int = 1):
        """Increment total events emitted."""
        self.events_emitted_total += count

    def increment_active_stream(self):
        """Increment active stream counter."""
        self.active_streams_count += 1

    def decrement_active_stream(self):
        """Decrement active stream counter."""
        self.active_streams_count = max(0, self.active_streams_count - 1)

    def record_stream_duration(self, duration_seconds: float):
        """Record stream duration."""
        self.stream_durations.append(duration_seconds)

    def get_summary(self) -> Dict[str, any]:
        """Get metrics summary."""
        avg_duration = (
            sum(self.stream_durations) / len(self.stream_durations)
            if self.stream_durations
            else 0
        )
        return {
            "agui_events_emitted_total": self.events_emitted_total,
            "agui_active_streams": self.active_streams_count,
            "agui_stream_duration_avg_seconds": round(avg_duration, 2),
            "agui_stream_count_total": len(self.stream_durations),
        }


# Global metrics instance
_metrics: Optional[StreamMetrics] = None


def get_metrics() -> StreamMetrics:
    """Get or create global metrics instance."""
    global _metrics
    if _metrics is None:
        _metrics = StreamMetrics()
    return _metrics


def emit_metrics_log():
    """Log current metrics (for monitoring/debugging)."""
    metrics = get_metrics()
    summary = metrics.get_summary()
    logger.info(
        f"[AGUIMetrics] Events: {summary['agui_events_emitted_total']}, "
        f"Active streams: {summary['agui_active_streams']}, "
        f"Avg duration: {summary['agui_stream_duration_avg_seconds']}s"
    )
