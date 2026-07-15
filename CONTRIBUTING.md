# Contributing to OpenWA Bridge

Thank you for considering contributing to OpenWA Bridge!

## Development Setup

### Prerequisites

- Frappe Framework v15+ / ERPNext v15+
- frappe_whatsapp app installed
- OpenWA Gateway running
- Python 3.10+
- Node.js 18+

### Setup

```bash
# Clone the repo into your bench apps directory
cd ~/frappe-bench/apps
git clone https://github.com/Manaa-Soft/openwa_bridge.git

# Install dependencies
cd ~/frappe-bench
bench --site your-site.local install-app openwa_bridge
bench --site your-site.local migrate
bench restart
```

## Code Style

### Python

- **Formatter**: ruff
- **Linter**: ruff
- **Type hints**: Use `from __future__ import annotations` at the top of every file
- **Docstrings**: Google style for all public functions and classes
- **Line length**: 120 characters max
- **Imports**: Sorted by ruff (isort-compatible)

Run linting:
```bash
cd ~/frappe-bench/apps/openwa_bridge
ruff check .
ruff format .
```

### JavaScript

- **Formatter**: prettier
- **Linter**: eslint

Run linting:
```bash
cd ~/frappe-bench/apps/openwa_bridge
npx prettier --write .
npx eslint .
```

## Pre-commit

Install pre-commit hooks:
```bash
cd ~/frappe-bench/apps/openwa_bridge
pre-commit install
```

This will run ruff, prettier, and eslint on every commit.

## Testing

### Running Tests

```bash
# Run all tests
bench --site your-site.local run-tests --app openwa_bridge

# Run specific test file
bench --site your-site.local run-tests --app openwa_bridge --module openwa_bridge.tests.test_utils

# Run specific test class
bench --site your-site.local run-tests --app openwa_bridge --module openwa_bridge.tests.test_circuit_breaker.TestOpenWACircuitBreaker
```

### Writing Tests

- Place tests in `openwa_bridge/tests/`
- Use `frappe.tests.IntegrationTestCase` as base class
- Mock external API calls (OpenWA) with `unittest.mock`
- Use fixtures from `conftest.py` for common test data
- Name test files `test_<module>.py`
- Name test classes `Test<Feature>`
- Name test methods `test_<behavior>`

### Test Categories

- **Unit tests**: Test individual functions in isolation (mock dependencies)
- **Integration tests**: Test full flows with Frappe database (require bench environment)

## Pull Request Process

1. Fork the repository
2. Create a feature branch from `develop`
3. Make your changes
4. Run linting and tests
5. Update documentation if needed
6. Submit a pull request

### PR Guidelines

- Keep PRs focused on a single change
- Include a clear description of what changed and why
- Add tests for new functionality
- Update CHANGELOG.md
- Ensure all tests pass

## Architecture Decisions

When making significant changes, document the rationale in `docs/` as an ADR (Architecture Decision Record).

## License

By contributing, you agree that your contributions will be licensed under the GPL-3.0 License.
