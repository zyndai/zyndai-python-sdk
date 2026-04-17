# Deployment Guide

This guide covers deploying ZyndAI agents to production, including configuration for proxies, timeouts, and AG-UI streaming.

## Proxy Configuration

When deploying agents behind a reverse proxy (nginx, Apache, etc.), ensure your proxy is configured to support AG-UI streaming with proper timeout settings.

### Nginx Configuration Example

```nginx
upstream agent_backend {
    server localhost:5000;
}

server {
    listen 80;
    server_name agent.example.com;

    location / {
        proxy_pass http://agent_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        
        # Important: Disable buffering for SSE streaming
        proxy_buffering off;
        proxy_cache off;
        
        # Set proper timeouts for long-lived streams
        # proxy_connect_timeout: Time to establish connection (default: 60s)
        proxy_connect_timeout 60s;
        
        # proxy_read_timeout: Time waiting for response data (default: 60s)
        # CRITICAL for AG-UI streams: Set to match or exceed stream timeout
        # If streams are configured with timeout=300s, set this to at least 300s
        proxy_read_timeout 600s;  # 10 minutes
        
        # proxy_send_timeout: Time waiting for client to accept data
        proxy_send_timeout 60s;
        
        # Pass through important headers
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Apache Configuration Example

```apache
ProxyRequests Off
ProxyPreserveHost On
ProxyPass / http://localhost:5000/
ProxyPassReverse / http://localhost:5000/

# Configure timeout for SSE streams
# Important: Match or exceed the stream timeout setting
ProxyTimeout 600  # 10 minutes
TimeOut 600       # Connection timeout

# Disable buffering for real-time events
SetEnv proxy-nokeepalive 1
SetEnv proxy-initial-handler default
```

## Stream Timeout Configuration

AG-UI streams are configured with a default timeout of 5 minutes (300 seconds). This timeout is:

1. **Set on the SDK side** (`zyndai_agent/ui/sse.py`): Streams remain open for the configured duration before closing
2. **Enforced on the proxy side**: The proxy must have a read timeout >= stream timeout
3. **Monitored on the client side**: MCP clients and n8n nodes have their own timeout handling

### Recommended Timeout Settings

| Component | Default | Recommended | Notes |
|-----------|---------|-------------|-------|
| Stream timeout (SDK) | 300s (5 min) | 300-600s | Configurable per stream |
| Proxy read_timeout | 60s | 600s+ | Must be >= stream timeout |
| Proxy connect_timeout | 60s | 60s | Typical value |
| Load balancer timeout | 60s | 600s+ | If behind load balancer |
| Client timeout (MCP) | 30s | 300s+ | Configurable per client |
| Client timeout (n8n) | Variable | Match stream timeout | Configure in node settings |

### Example: Long-Running Streams

For agents that process tasks longer than 5 minutes:

```python
from zyndai_agent import ZyndAIAgent, AgentConfig

config = AgentConfig(
    name="Long Task Agent",
    webhook_port=5000,
    generative_ui=True,
)

agent = ZyndAIAgent(agent_config=config)

@agent.register_handler
async def handle_long_task(message, ui):
    await ui.text("Starting long task...")
    
    # Task takes 10 minutes
    for i in range(600):
        await asyncio.sleep(1)
        if i % 60 == 0:
            await ui.text(f"Progress: {i//60} minutes...")
```

And configure the stream timeout:

```python
# Client side: n8n node
# Set Stream Timeout to 600 seconds (10 minutes)

# Or: MCP client
import asyncio
from zyndai_mcp_server import tools

result = await tools.zyndai_subscribe_agent_stream(
    agent_id="...",
    timeout_seconds=600  # 10 minutes
)
```

## Rate Limiting

AG-UI streams are rate-limited to prevent abuse:

- **Default limit**: 10 concurrent streams per IP address per minute
- **Enforcement**: Applied at the agent webhook level (not the proxy)

If you need to adjust these limits, modify the `StreamRateLimiter` initialization in `webhook_communication.py`:

```python
self._stream_rate_limiter = StreamRateLimiter(
    max_streams_per_ip=10,      # Adjust as needed
    window_seconds=60            # Time window in seconds
)
```

## Health Checks and Monitoring

### Health Check Endpoint

```bash
curl http://agent.example.com/health
# Returns: { "status": "healthy", "agent_id": "...", "uptime_seconds": 123 }
```

### Metrics Endpoint

Stream metrics are tracked in-memory and can be accessed via:

```python
from zyndai_agent.ui.metrics import get_metrics

metrics = get_metrics()
print(metrics.get_summary())
# Output:
# {
#     "agui_events_emitted_total": 1523,
#     "agui_active_streams": 2,
#     "agui_stream_duration_avg_seconds": 45.3,
#     "agui_stream_count_total": 87
# }
```

## DID Signature Verification

When deploying with signature verification enabled, ensure:

1. **Agent has a valid DID**: Generated during registration
2. **Clients have the agent's public key**: For verifying stream signatures
3. **Nginx/proxy passes through query parameters**: For DID and signature in `/ui/stream/<conversation_id>?sender_did=...&signature=...`

Example client request with signature:

```bash
curl "http://agent.example.com/ui/stream/conv-123?sender_did=did:key:z6MkhaXgBZDvotpK&signature=base64_signature"
```

## Production Deployment Checklist

- [ ] Proxy configured with `proxy_buffering off` and `proxy_read_timeout >= stream timeout`
- [ ] Stream timeout set to match expected task duration
- [ ] Rate limiting limits reviewed and adjusted if needed
- [ ] Health checks configured on load balancer
- [ ] Metrics monitoring in place (Prometheus, DataDog, etc.)
- [ ] Graceful shutdown configured (SIGTERM → emit RUN_ERROR)
- [ ] DID verification enabled for security
- [ ] SSL/TLS certificate configured for HTTPS
- [ ] Ngrok tunnel or public URL configured and verified
- [ ] Error logs monitored for connection issues

## Troubleshooting

### "Stream timeout after 5 minutes"

**Cause**: Proxy read_timeout is less than stream timeout

**Solution**: Increase proxy `read_timeout` to >= stream timeout

```nginx
proxy_read_timeout 600s;  # 10 minutes
```

### "Rate limit exceeded. Max 10 concurrent streams per IP per minute"

**Cause**: Client exceeded rate limit

**Solution**:
1. Wait before opening more streams
2. Increase rate limit if legitimate use:

```python
self._stream_rate_limiter = StreamRateLimiter(
    max_streams_per_ip=50,  # Increased from 10
    window_seconds=60
)
```

### "Invalid signature" when subscribing to stream

**Cause**: Sender DID or signature is invalid or missing

**Solution**:
1. Include `sender_did` and `signature` query parameters
2. Ensure client has agent's public key for verification
3. Check signature generation code in client

### Stream closes unexpectedly

**Cause**: Agent is shutting down or error occurred

**Check**: Look for RUN_ERROR event in stream output. See server logs for details.
