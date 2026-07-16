# Use an official lightweight Python image
FROM python:3.11-slim

# Set environment variables to optimize Python inside Docker
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH

# Create a non-root user for Hugging Face security compliance
RUN useradd -m -u 1000 user
WORKDIR $HOME/app

# Copy requirements first to leverage Docker layer caching
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --upgrade -r requirements.txt

# Copy the rest of the application code into the container
COPY --chown=user . .

# Make the startup script executable
RUN chmod +x start.sh

# Switch to the non-root user
USER user

# Hugging Face Spaces strictly listen on port 7860
EXPOSE 7860

# Execute the startup script
CMD ["./start.sh"]
