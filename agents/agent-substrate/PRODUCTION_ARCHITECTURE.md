# Production Architecture

## Overview
Unified backend architecture for production deployment with horizontal scaling capabilities.

## System Components

### 1. Reverse Proxy (nginx)
- **Purpose**: Single entry point, routes traffic to appropriate services
- **Port**: 443 (HTTPS)
- **Routing**:
  - `/` → Next.js frontend (port 3001)
  - `/api/chat/*` → Python FastAPI (port 8001)
  - `/api/mcp/*` → Python FastAPI (port 8001)
  - `/api/auth/*` → Next.js OAuth handlers (port 3001)

### 2. Frontend (Next.js)
- **Port**: 3001
- **Responsibilities**:
  - Chat UI rendering
  - OAuth flows (Google, Spotify)
  - Session management
  - API proxy to Python backend
- **Tech Stack**: Next.js 16, React, TypeScript, Prisma client

### 3. Backend (Python FastAPI)
- **Port**: 8001
- **Responsibilities**:
  - Agent execution (LangGraph/LangChain)
  - MCP tool orchestration
  - Credential management & encryption
  - Tool result caching
- **Tech Stack**: FastAPI, SQLAlchemy, cryptography, agent-framework

### 4. Database (PostgreSQL)
- **Port**: 5432
- **Schema**:
  ```sql
  -- Users table
  CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    google_id VARCHAR(255) UNIQUE,
    name VARCHAR(255),
    avatar_url TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
  );

  -- OAuth credentials (encrypted)
  CREATE TABLE user_credentials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    provider VARCHAR(50) NOT NULL, -- 'spotify', 'google', 'netflix'
    access_token TEXT NOT NULL, -- AES-256 encrypted
    refresh_token TEXT, -- AES-256 encrypted
    token_type VARCHAR(50),
    expires_at TIMESTAMP,
    scope TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, provider)
  );

  -- Sessions
  CREATE TABLE sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    token VARCHAR(255) UNIQUE NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
  );

  -- Chat history
  CREATE TABLE conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
  );

  CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL, -- 'user', 'assistant', 'system'
    content TEXT NOT NULL,
    tool_calls JSONB,
    created_at TIMESTAMP DEFAULT NOW()
  );

  -- Indexes
  CREATE INDEX idx_user_credentials_user_id ON user_credentials(user_id);
  CREATE INDEX idx_sessions_token ON sessions(token);
  CREATE INDEX idx_sessions_user_id ON sessions(user_id);
  CREATE INDEX idx_messages_conversation_id ON messages(conversation_id);
  ```

## Authentication Flow

### OAuth (Spotify/Google)
1. User clicks "Connect Spotify" in Next.js UI
2. Next.js `/api/spotify/login` redirects to Spotify OAuth
3. Spotify redirects back to `/api/spotify/callback?code=...`
4. Next.js:
   - Exchanges code for tokens
   - Encrypts tokens
   - Stores in PostgreSQL via Prisma
   - Creates session token
   - Sets httpOnly session cookie
5. User makes chat request
6. Next.js forwards to Python with session token in header
7. Python validates session, fetches decrypted credentials from DB
8. Python executes tool with user's credentials

### Session Flow
```
Frontend Request → nginx → Next.js → Check Session Cookie
                                     ↓
                             Query PostgreSQL for user
                                     ↓
                             Forward to Python with user_id
                                     ↓
                             Python fetches credentials from DB
                                     ↓
                             Execute tool with user context
```

## Credential Encryption

### Encryption Service (Python)
```python
from cryptography.fernet import Fernet
import os

class CredentialService:
    def __init__(self):
        # Load from environment (rotate regularly)
        self.key = os.environ["ENCRYPTION_KEY"].encode()
        self.cipher = Fernet(self.key)
    
    def encrypt_token(self, token: str) -> str:
        return self.cipher.encrypt(token.encode()).decode()
    
    def decrypt_token(self, encrypted: str) -> str:
        return self.cipher.decrypt(encrypted.encode()).decode()
    
    async def store_credential(self, user_id: str, provider: str, tokens: dict):
        """Store encrypted credentials in database"""
        encrypted_access = self.encrypt_token(tokens["access_token"])
        encrypted_refresh = self.encrypt_token(tokens.get("refresh_token", ""))
        
        await db.execute("""
            INSERT INTO user_credentials (user_id, provider, access_token, refresh_token, expires_at)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (user_id, provider) 
            DO UPDATE SET 
                access_token = $3,
                refresh_token = $4,
                expires_at = $5,
                updated_at = NOW()
        """, user_id, provider, encrypted_access, encrypted_refresh, expires_at)
    
    async def get_credential(self, user_id: str, provider: str) -> dict:
        """Fetch and decrypt credentials"""
        row = await db.fetchrow("""
            SELECT access_token, refresh_token, expires_at
            FROM user_credentials
            WHERE user_id = $1 AND provider = $2
        """, user_id, provider)
        
        if not row:
            return None
        
        # Check expiry, refresh if needed
        if row["expires_at"] < datetime.utcnow():
            await self.refresh_token(user_id, provider)
            return await self.get_credential(user_id, provider)
        
        return {
            "access_token": self.decrypt_token(row["access_token"]),
            "refresh_token": self.decrypt_token(row["refresh_token"]) if row["refresh_token"] else None
        }
```

## Deployment

### Development (Docker Compose)
```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: agent_framework
      POSTGRES_USER: agent
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"

  backend:
    build: ./agent-framework
    environment:
      DATABASE_URL: postgresql://agent:${DB_PASSWORD}@postgres:5432/agent_framework
      ENCRYPTION_KEY: ${ENCRYPTION_KEY}
      OPENAI_API_KEY: ${OPENAI_API_KEY}
    ports:
      - "8001:8001"
    depends_on:
      - postgres

  frontend:
    build: ./ai-chatbot-ui
    environment:
      DATABASE_URL: postgresql://agent:${DB_PASSWORD}@postgres:5432/agent_framework
      NEXTAUTH_SECRET: ${NEXTAUTH_SECRET}
      SPOTIFY_CLIENT_ID: ${SPOTIFY_CLIENT_ID}
      SPOTIFY_CLIENT_SECRET: ${SPOTIFY_CLIENT_SECRET}
    ports:
      - "3001:3001"
    depends_on:
      - postgres

  nginx:
    image: nginx:alpine
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
    ports:
      - "443:443"
      - "80:80"
    depends_on:
      - frontend
      - backend

volumes:
  postgres_data:
```

### Production (Kubernetes)
```yaml
# Horizontal Pod Autoscaler for Python backend
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: agent-backend-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: agent-backend
  minReplicas: 2
  maxReplicas: 10
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
```

## Scaling Strategy

### Phase 1: Single Server (1-100 users)
- 1 nginx instance
- 1 Next.js instance
- 1 Python FastAPI instance
- 1 PostgreSQL instance

### Phase 2: Horizontal Scaling (100-10k users)
- 1 Load Balancer
- 2+ Next.js instances
- 3+ Python FastAPI instances (CPU-bound agent work)
- 1 PostgreSQL primary + 2 read replicas
- Redis for session caching

### Phase 3: Microservices (10k+ users)
- API Gateway (Kong)
- Separate Auth Service (Node.js)
- Agent Service (Python, multiple workers)
- Database cluster (Patroni/Citus)
- Message queue (RabbitMQ/Kafka) for async tasks

## Security Considerations

1. **Credentials**: AES-256 encryption at rest, rotate keys quarterly
2. **Sessions**: httpOnly, secure, SameSite=Strict cookies
3. **HTTPS**: Required in production (Let's Encrypt)
4. **Rate Limiting**: nginx rate limits per IP
5. **SQL Injection**: Use parameterized queries only
6. **CSRF**: Token validation on state-changing requests
7. **Secrets**: Environment variables, never commit to git

## Monitoring

- **Logs**: Centralized logging (ELK stack or Datadog)
- **Metrics**: Prometheus + Grafana
- **Tracing**: OpenTelemetry (already in agent-framework)
- **Alerts**: 
  - Failed OAuth flows
  - High error rates
  - Credential decryption failures
  - Database connection issues

## Cost Estimation (Monthly)

### Development
- Local development: $0

### Production (AWS)
- **Small (100 users)**:
  - EC2 t3.medium (2 vCPU, 4GB): $30
  - RDS PostgreSQL db.t3.micro: $15
  - Load Balancer: $15
  - **Total: ~$60/month**

- **Medium (10k users)**:
  - ECS Fargate (2 containers): $100
  - RDS PostgreSQL db.t3.small + replica: $60
  - ElastiCache Redis: $15
  - ALB: $25
  - **Total: ~$200/month**

- **Large (100k users)**:
  - EKS cluster (3 nodes): $200
  - RDS PostgreSQL db.r5.large + 2 replicas: $500
  - ElastiCache cluster: $100
  - CloudFront CDN: $50
  - **Total: ~$850/month**
