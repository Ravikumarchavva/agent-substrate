import { PrismaClient } from '@prisma/client'

const globalForPrisma = globalThis as unknown as {
  prisma: PrismaClient | undefined
}

export const prisma = globalForPrisma.prisma ?? new PrismaClient({
  // Every proxied request looks up its session, and the page polls: logging each query fills the terminal. PRISMA_LOG_QUERIES=1 turns it back on.
  log: process.env.NODE_ENV === 'development' ? (process.env.PRISMA_LOG_QUERIES === '1' ? ['query', 'error', 'warn'] : ['error', 'warn']) : ['error'],
})

if (process.env.NODE_ENV !== 'production') globalForPrisma.prisma = prisma

export default prisma
