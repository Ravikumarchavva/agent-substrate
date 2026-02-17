# Production Setup Guide

## Quick Start

### Prerequisites
- Docker & Docker Compose
- OpenAI API key
- Spotify Developer account
- SSL certificates (or use self-signed for development)

### 1. Generate Encryption Key

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy the output and save it for the `.env` file.

### 2. Configure OAuth Applications

#### Spotify
1. Go to https://developer.spotify.com/dashboard
2. Create a new app or use existing
3. Add Redirect URI: `https://127.0.0.1/api/spotify/callback`
4. Copy Client ID and Client Secret

#### Google (Optional)
1. Go to https://console.cloud.google.com/apis/credentials
2. Create OAuth 2.0 Client ID
3. Add Redirect URI: `https://127.0.0.1/api/auth/google/callback`
4. Copy Client ID and Client Secret

### 3. Create Environment File

```bash
cd agent-framework
cp .env.example .env
```

Edit `.env` and fill in:
```env
# Required
DATABASE_URL=postgresql://agent:your-strong-password@postgres:5432/agent_framework
DB_PASSWORD=your-strong-password
ENCRYPTION_KEY=<from-step-1>
NEXTAUTH_SECRET=<random-32-char-string>
OPENAI_API_KEY=sk-...
SPOTIFY_CLIENT_ID=<from-spotify-dashboard>
SPOTIFY_CLIENT_SECRET=<from-spotify-dashboard>

# Optional
GOOGLE_CLIENT_ID=<from-google-console>
GOOGLE_CLIENT_SECRET=<from-google-console>
```

### 4. Generate SSL Certificates

For development (self-signed):
```bash
cd agent-framework
mkdir -p ssl
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout ssl/key.pem -out ssl/cert.pem \
  -subj "/CN=127.0.0.1"
```

For production, use Let's Encrypt:
```bash
certbot certonly --standalone -d yourdomain.com
cp /etc/letsencrypt/live/yourdomain.com/fullchain.pem ssl/cert.pem
cp /etc/letsencrypt/live/yourdomain.com/privkey.pem ssl/key.pem
```

### 5. Set up Database

Create Prisma client and run migrations:

```bash
cd ../ai-chatbot-ui
pnpm install
pnpm prisma generate
pnpm prisma migrate dev --name init
```

### 6. Start Services

```bash
cd ../agent-framework
docker-compose -f docker-compose.production.yml up -d
```

This starts:
- PostgreSQL (port 5432)
- Python backend (port 8001)
- Next.js frontend (port 3001)
- Nginx reverse proxy (ports 80, 443)

### 7. Verify Setup

```bash
# Check all services are running
docker-compose -f docker-compose.production.yml ps

# Check logs
docker-compose -f docker-compose.production.yml logs -f

# Test health endpoint
curl https://127.0.0.1/health
```

### 8. Access Application

Open https://127.0.0.1 in your browser.

If using self-signed cert, you'll see a warning - click "Advanced" and proceed.

---

## Development Setup (Without Docker)

### 1. Set up PostgreSQL

```bash
# Install PostgreSQL 16
# Windows: https://www.postgresql.org/download/windows/
# Mac: brew install postgresql@16
# Linux: apt-get install postgresql-16

# Start PostgreSQL
pg_ctl start

# Create database
createdb agent_framework
```

### 2. Set up Next.js Frontend

```bash
cd ai-chatbot-ui
pnpm install
pnpm prisma generate
pnpm prisma migrate dev
pnpm dev
```

Frontend runs on http://127.0.0.1:3001

### 3. Set up Python Backend

```bash
cd agent-framework/src/agent_framework
uv venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
uv pip install -e ../../
uv run server/app.py
```

Backend runs on http://127.0.0.1:8001

### 4. Test Without nginx

For development, you can skip nginx and test each service directly:
- Frontend: http://127.0.0.1:3001
- Backend: http://127.0.0.1:8001/docs (FastAPI docs)

**Note**: This will have CORS issues with MCP apps. For full testing, use nginx.

---

## Database Management

### Create migration
```bash
cd ai-chatbot-ui
pnpm prisma migrate dev --name add_new_feature
```

### View database
```bash
pnpm prisma studio
```

### Reset database (WARNING: deletes all data)
```bash
pnpm prisma migrate reset
```

### Backup database
```bash
docker exec agent-db pg_dump -U agent agent_framework > backup.sql
```

### Restore database
```bash
docker exec -i agent-db psql -U agent agent_framework < backup.sql
```

---

## Monitoring

### View logs
```bash
# All services
docker-compose -f docker-compose.production.yml logs -f

# Specific service
docker-compose -f docker-compose.production.yml logs -f backend
docker-compose -f docker-compose.production.yml logs -f frontend
docker-compose -f docker-compose.production.yml logs -f nginx
```

### Check resource usage
```bash
docker stats
```

### Access database
```bash
docker exec -it agent-db psql -U agent agent_framework
```

---

## Deployment

### AWS EC2

1. **Launch EC2 instance**:
   - AMI: Ubuntu 22.04 LTS
   - Instance type: t3.medium (minimum)
   - Security groups: Allow 80, 443, 22

2. **Install Docker**:
```bash
sudo apt update
sudo apt install -y docker.io docker-compose
sudo usermod -aG docker ubuntu
```

3. **Clone repository**:
```bash
git clone https://github.com/yourusername/agent-framework.git
cd agent-framework
```

4. **Set up environment**:
```bash
cp .env.example .env
nano .env  # Fill in production values
```

5. **Start services**:
```bash
docker-compose -f docker-compose.production.yml up -d
```

6. **Set up domain** (optional):
   - Point domain to EC2 public IP
   - Update nginx.conf with your domain
   - Get Let's Encrypt certificate
   - Restart nginx

### Vercel + Railway

Alternatively, deploy separately:

1. **Database**: Railway PostgreSQL
2. **Backend**: Railway (Python app)
3. **Frontend**: Vercel (Next.js)

Update environment variables to point to Railway services.

---

## Scaling

### Phase 1: Vertical Scaling
- Increase EC2 instance size (t3.large, t3.xlarge)
- Increase database instance size

### Phase 2: Horizontal Scaling
- Add more Python backend instances
- Set up PostgreSQL read replicas
- Use Redis for session storage
- Add load balancer

### Phase 3: Microservices
- Separate auth service
- Message queue for async tasks (RabbitMQ)
- Kubernetes orchestration

---

## Troubleshooting

### OAuth not working
1. Check Spotify/Google dashboard redirect URIs match exactly
2. Verify CORS settings in nginx.conf
3. Check browser console for errors
4. Ensure cookies are set (check developer tools → Application → Cookies)

### Database connection errors
1. Verify DATABASE_URL in .env
2. Check PostgreSQL is running: `docker ps`
3. Test connection: `docker exec -it agent-db psql -U agent agent_framework`
4. Check logs: `docker logs agent-db`

### MCP apps not loading
1. Check Python backend is running: `curl http://127.0.0.1:8001/health`
2. Verify nginx routing: `docker logs agent-nginx`
3. Check browser console for iframe errors

### Token decryption errors
1. Verify ENCRYPTION_KEY matches between frontend and backend
2. Check database contains encrypted tokens: `SELECT * FROM user_credentials;`
3. Regenerate encryption key and re-authenticate

---

## Security Checklist

- [ ] Use strong passwords for database
- [ ] Rotate encryption key quarterly
- [ ] Enable HTTPS in production (Let's Encrypt)
- [ ] Set secure=true for cookies in production
- [ ] Enable rate limiting in nginx
- [ ] Keep Docker images updated
- [ ] Regular database backups
- [ ] Monitor for failed login attempts
- [ ] Use secrets manager for production (AWS Secrets Manager, HashiCorp Vault)
- [ ] Enable database encryption at rest
- [ ] Set up firewall rules (only allow 80, 443, 22)
- [ ] Disable SSH password authentication (use keys only)

---

## Support

For issues, check:
1. Docker logs: `docker-compose logs -f`
2. Browser console (F12)
3. FastAPI docs: https://127.0.0.1/api/chat/docs
4. Database: `docker exec -it agent-db psql -U agent agent_framework`

Common commands:
```bash
# Restart all services
docker-compose -f docker-compose.production.yml restart

# Rebuild after code changes
docker-compose -f docker-compose.production.yml up -d --build

# Stop all services
docker-compose -f docker-compose.production.yml down

# Stop and remove volumes (WARNING: deletes data)
docker-compose -f docker-compose.production.yml down -v
```
