# Contributing to Zep

Thank you for your interest in contributing to Zep! We appreciate your efforts and look forward to collaborating with you.

### Getting Started

1. **Fork and Clone**: Start by forking the [Zep repo](https://github.com/getzep/zep). Then, clone your fork locally:

```
git clone https://github.com/<your-github-username>/zep.git
```

2. **Set Upstream**: Keep your fork synced with the upstream repo by adding it as a remote:

```
git remote add upstream https://github.com/getzep/zep.git
```

3. **Create a Feature Branch**: Always create a new branch for your work. This helps in keeping your changes organized and easier for maintainers to review.

```
git checkout -b feature/your-feature-name
```

### Setting "Development" Mode
"Development" mode forces Zep's log level to "debug" and disables caching of the web UI. This is useful when developing Zep locally.

To enable "development" mode, set the `ZEP_DEVELOPMENT` environment variable to `true`:

```
export ZEP_DEVELOPMENT=true
```

or modify your `.env` file accordingly.


### Running the Database and NLP Server Stack

A development stack can be started by running:

 ```bash
 make dev
 ```

This starts the DB and NLP services using docker compose and exposes the DB on port 5432 and the NLP service on port 5557. 
The database volume is also not persistent, so it will be wiped out when the stack is stopped.

### Automatically Rebuilding Zep using Go Watch

**Note:** You will need to have [Go Watch](https://github.com/mitranim/gow) installed.

If you want to automatically rebuild Zep when you make changes to the code, run:

```
make watch
```

The above sets "Development" mode and binds Zep to localhost only.


### Rebuilding Tailwind CSS

If you make changes to the CSS used by HTML template files, you will need to rebuild the Tailwind CSS file. 

Run:
```
make web
```

### Building Zep

Follow these steps to build Zep locally:

1. Navigate to the project root:

```
cd zep
```

2. Build the project:

```
make build
```

This will produce the binary in `./out/bin`.

### Running Tests

It's essential to ensure that your code passes all tests. Run the tests using:

```
make test
```

If you want to check the coverage, run:

```
make coverage
```

### Code Linting

Ensure your code adheres to our linting standards:

```
make lint
```

### Generating Swagger Docs

If you make changes to the API or its documentation, regenerate the Swagger docs:

```
make swagger
```

### Submitting Changes

1. **Commit Your Changes**: Use meaningful commit messages that describe the changes made.

```
git add .
git commit -m "Your detailed commit message"
```

2. **Push to Your Fork**:

```
git push origin feature/your-feature-name
```

3. **Open a Pull Request**: Navigate to the [Zep GitHub repo](https://github.com/getzep/zep) and click on "New pull request". Choose your fork and the branch you've been working on. Submit the PR with a descriptive message.

### Feedback

Maintainers will review your PR and provide feedback. If any changes are needed, make them in your feature branch and push to your fork. The PR will update automatically.

### Final Notes

- Always be respectful and kind.
- If you're unsure about something, ask. We're here to help.
- Once again, thank you for contributing to Zep!

---

If you encounter any issues or have suggestions, please open an issue!