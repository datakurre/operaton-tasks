# operaton-tasks

WIP external task library and worker for https://operaton.org/

## Authentication

The worker supports two authentication modes for Operaton REST API requests:

1. Static authorization header via ENGINE_REST_AUTHORIZATION.
2. OAuth2 client credentials via OAUTH2_CLIENT_ID, OAUTH2_CLIENT_SECRET, and OAUTH2_TOKEN_URL.

When OAuth2 is configured, it takes precedence over ENGINE_REST_AUTHORIZATION for automatic Bearer token acquisition and refresh.

### OAuth2 Environment Variables

- OAUTH2_CLIENT_ID
- OAUTH2_CLIENT_SECRET
- OAUTH2_TOKEN_URL
- OAUTH2_SCOPES (optional, space-separated)

Local Keycloak token endpoint example:
http://localhost:8081/realms/operaton/protocol/openid-connect/token

### CLI

The operaton-tasks command uses the main CLI entrypoint and accepts OAuth2 options via serve:

- --oauth2-client-id
- --oauth2-client-secret
- --oauth2-token-url
- --oauth2-scopes