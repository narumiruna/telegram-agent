[default]
all: format lint type test

# Format code using ruff
format:
    uv run ruff format

# Lint code using ruff
lint:
    uv run ruff check --fix

# Type checking using ty
type:
    uv run ty check

# Run tests using pytest with coverage
test:
    uv run pytest -v -s --cov=src tests

# Build Docker image through Compose
compose-build:
    docker compose build

# Start bot with Docker Compose
compose-up:
    docker compose up -d --build

# Stop Docker Compose services
compose-down:
    docker compose down

# Follow bot logs
compose-logs:
    docker compose logs -f telegramagent

# Restart bot container
compose-restart:
    docker compose restart telegramagent

# Show Docker Compose service status
compose-ps:
    docker compose ps

# Build and publish the package to PyPI
publish:
    uv build
    uv publish
