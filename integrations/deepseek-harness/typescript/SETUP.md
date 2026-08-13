# Setup

## 1. Create a Zep account and API key

1. Sign up at [getzep.com](https://www.getzep.com).
2. Open the [Zep dashboard](https://app.getzep.com).
3. Create or select a project and create an API key.

## 2. Install the plugin

Install the package into the DeepSeek Harness profile that should use memory:

```bash
dsh plugin add --profile headless @getzep/zep-deepseek-harness
```

Replace `headless` with `web` or another profile name as needed. The package
contains a Harness bundle, so installation activates its `zep-memory` Cordis
row automatically.

## 3. Configure identity

The bundled row reads:

```bash
export ZEP_API_KEY="your-zep-api-key"
export ZEP_USER_ID="stable-id-for-the-human-user"
```

Use a stable application user id, not a random id per process. For stronger
identity resolution, update the profile's `cordis.patch.yml` row:

```yaml
- id: zep-memory
  name: '@getzep/zep-deepseek-harness'
  config:
    apiKey: !!js process.env.ZEP_API_KEY
    userId: !!js process.env.ZEP_USER_ID
    firstName: Ada
    lastName: Lovelace
    email: ada@example.com
    threadIdPrefix: dsh-
```

Do not commit an API key to YAML. `!!js process.env.ZEP_API_KEY` resolves it
from the process environment when the plugin loads.

By default, the Harness session id becomes the Zep thread id. Every session
therefore gets its own thread while sharing the user's graph. Set a fixed
`threadId` only when one plugin instance is intentionally scoped to one
conversation.

## 4. Run Harness

```bash
dsh --profile headless "Remember that I prefer aisle seats."
```

After Zep has ingested the turn, start or continue a session for the same
`ZEP_USER_ID` and ask what the agent remembers. Zep ingestion is asynchronous,
so a just-written fact may not be available immediately.

## 5. Verify the package

From this integration directory:

```bash
npm install
npm run lint
npm run typecheck
npm test
npm run build
```

The tests use a mocked Zep client and require no credentials.
