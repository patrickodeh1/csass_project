FROM python:3.11-slim

ENV PYTHONUNBUFFERED True
ENV APP_HOME /app
WORKDIR $APP_HOME

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create staticfiles directory
RUN mkdir -p staticfiles

# Set environment variable for collectstatic
ENV DEBUG=True
ENV SECRET_KEY=temp-build-key

# Collect static files
RUN python manage.py collectstatic --noinput --clear

# Create empty media directory
RUN mkdir -p media

# Run the web service on container startup
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 csass_project.wsgi:application