import crypto from 'crypto';
import prisma from './prisma';

const ALGORITHM = 'aes-256-gcm';
const KEY_LENGTH = 32;
const IV_LENGTH = 16;
const AUTH_TAG_LENGTH = 16;

class CredentialManager {
  private key: Buffer;

  constructor() {
    const encryptionKey = process.env.ENCRYPTION_KEY;
    if (!encryptionKey) {
      throw new Error('ENCRYPTION_KEY environment variable not set');
    }
    // Derive a 32-byte key from the encryption key
    this.key = crypto.createHash('sha256').update(encryptionKey).digest();
  }

  encrypt(text: string): string {
    const iv = crypto.randomBytes(IV_LENGTH);
    const cipher = crypto.createCipheriv(ALGORITHM, this.key, iv);
    
    let encrypted = cipher.update(text, 'utf8', 'hex');
    encrypted += cipher.final('hex');
    
    const authTag = cipher.getAuthTag();
    
    // Combine IV + AuthTag + EncryptedData
    return iv.toString('hex') + authTag.toString('hex') + encrypted;
  }

  decrypt(encryptedText: string): string {
    // Extract IV, AuthTag, and encrypted data
    const iv = Buffer.from(encryptedText.slice(0, IV_LENGTH * 2), 'hex');
    const authTag = Buffer.from(
      encryptedText.slice(IV_LENGTH * 2, IV_LENGTH * 2 + AUTH_TAG_LENGTH * 2),
      'hex'
    );
    const encrypted = encryptedText.slice(IV_LENGTH * 2 + AUTH_TAG_LENGTH * 2);
    
    const decipher = crypto.createDecipheriv(ALGORITHM, this.key, iv);
    decipher.setAuthTag(authTag);
    
    let decrypted = decipher.update(encrypted, 'hex', 'utf8');
    decrypted += decipher.final('utf8');
    
    return decrypted;
  }

  async storeCredential(
    userId: string,
    provider: string,
    accessToken: string,
    refreshToken?: string,
    expiresIn: number = 3600,
    scope?: string
  ): Promise<void> {
    const encryptedAccess = this.encrypt(accessToken);
    const encryptedRefresh = refreshToken ? this.encrypt(refreshToken) : null;
    const expiresAt = new Date(Date.now() + expiresIn * 1000);

    await prisma.userCredential.upsert({
      where: {
        userId_provider: {
          userId,
          provider,
        },
      },
      update: {
        accessToken: encryptedAccess,
        refreshToken: encryptedRefresh,
        expiresAt,
        scope,
        updatedAt: new Date(),
      },
      create: {
        userId,
        provider,
        accessToken: encryptedAccess,
        refreshToken: encryptedRefresh,
        tokenType: 'Bearer',
        expiresAt,
        scope,
      },
    });
  }

  async getCredential(
    userId: string,
    provider: string
  ): Promise<{ accessToken: string; refreshToken?: string; expiresAt: Date } | null> {
    const credential = await prisma.userCredential.findUnique({
      where: {
        userId_provider: {
          userId,
          provider,
        },
      },
    });

    if (!credential) {
      return null;
    }

    // Check if expired
    if (credential.expiresAt && credential.expiresAt < new Date()) {
      // Token expired - should refresh
      return null;
    }

    return {
      accessToken: this.decrypt(credential.accessToken),
      refreshToken: credential.refreshToken ? this.decrypt(credential.refreshToken) : undefined,
      expiresAt: credential.expiresAt!,
    };
  }

  async deleteCredential(userId: string, provider: string): Promise<boolean> {
    try {
      await prisma.userCredential.delete({
        where: {
          userId_provider: {
            userId,
            provider,
          },
        },
      });
      return true;
    } catch {
      return false;
    }
  }

  async getUserProviders(userId: string): Promise<string[]> {
    const credentials = await prisma.userCredential.findMany({
      where: { userId },
      select: { provider: true },
    });
    return credentials.map((c) => c.provider);
  }
}

// Singleton instance
let credentialManager: CredentialManager | null = null;

export function getCredentialManager(): CredentialManager {
  if (!credentialManager) {
    credentialManager = new CredentialManager();
  }
  return credentialManager;
}

export default getCredentialManager;
