export type User = {
  id: string;
  name: string;
  email: string;
  avatar?: string;
};

export interface AuthUser {
  email?: string;
  name?: string;
  isAdmin?: boolean;
  /** The Keycloak organization (project) this session is for. */
  tenantId?: string;
}

export type Element = {
  id: string;
  thread_id?: string;
  type?: string;
  name: string;
  mime?: string;
  size?: string;
  display?: string;
  url?: string;
  for_id?: string;
  props?: Record<string, unknown>;
};

/** Result of validating custom instructions before persisting them locally. */
export interface InstructionValidationResult {
  allowed: boolean;
  reason?: string;
}
