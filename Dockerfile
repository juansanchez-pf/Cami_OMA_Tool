# Use an official lightweight Python image
FROM python:3.9-slim

# Set the working directory inside the container
WORKDIR /app

# Copy the requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code
COPY . .

# Expose the port Cloud Run expects
EXPOSE 8080

# Command to run the Streamlit app
CMD ["streamlit", "run", "Python-Code", "--server.port=8080", "--server.address=0.0.0.0"]
