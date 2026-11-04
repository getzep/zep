# TypeScript user management example

This script shows user management with the Zep v4 TypeScript SDK. It creates users, updates and deletes users created by the script, and lists users in the project.

## Requirements

- Node.js 18 or later.
- Shared project packages: `@getzep/zep-cloud@^4.0.0`, `uuid@^11.1.1`, `@types/node@^22.13.10`, `tsx@^4.19.3`, and `typescript@^5.8.2`.
- Environment variable: `ZEP_API_KEY`.

## Run

From `examples/typescript/`, install the packages and run the script:

```bash
npm install
npm run example:users
```

## What the example does

- Creates three users. The server returns a UUID for each user.
- Updates the first user by UUID.
- Deletes the second user created by the script.
- Lists up to 10 users from the first page.
