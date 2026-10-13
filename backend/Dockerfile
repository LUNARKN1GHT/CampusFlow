FROM python:3.12-slim-bookworm

RUN pip install --no-cache-dir uv==0.12.19
WORKDIR /app
COPY pyproject.toml uv.lock .python-version alembic.ini ./
COPY src ./src
COPY migrations ./migrations
RUN uv sync --locked --no-dev
ENV PYTHONUNBUFFERED=1
CMD ["uv", "run", "--locked", "--no-dev", "python", "-m", "campusflow.workers.main"]
